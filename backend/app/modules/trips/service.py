"""Trip scheduling and the driver's start -> arrive -> end workflow.

Public API for other modules: get_trip, get_trip_for_update, next_stop, trip_detail(s), active_trips,
trips_for_route_on, driver_trips, route_seat_capacity, route_seat_capacities, stops_after,
close_stale_trips, can_view_trip, ensure_can_view.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.config import settings
from app.core.db import flush_or_conflict
from app.core.deps import Principal
from app.core.errors import Conflict, Forbidden, InvalidState, NotFound
from app.core.events import DomainEvent
from app.core.roles import Role
from app.core.timeutil import local_to_utc, minutes_between, now_utc, today_local
from app.modules.auth import service as auth_service
from app.modules.master_data import service as md_service
from app.modules.master_data.models import Bus, BusStatus, Route
from app.modules.trips.models import Direction, Trip, TripSchedule, TripStatus, TripStopEvent
from app.modules.trips.schemas import (
    BusBrief,
    DriverBrief,
    GenerateOut,
    RouteBrief,
    ScheduleIn,
    ScheduleUpdate,
    StopEventOut,
    TripDetail,
    TripOut,
)


# ---------------- Schedules ----------------
async def list_schedules(
    session: AsyncSession, *, route_id: int | None = None, driver_id: int | None = None
) -> list[TripSchedule]:
    stmt = select(TripSchedule).order_by(TripSchedule.departure_time)
    if route_id:
        stmt = stmt.where(TripSchedule.route_id == route_id)
    if driver_id:
        stmt = stmt.where(TripSchedule.driver_id == driver_id)
    return list(await session.scalars(stmt))


async def get_schedule(session: AsyncSession, schedule_id: int) -> TripSchedule:
    s = await session.get(TripSchedule, schedule_id)
    if s is None:
        raise NotFound(f"Schedule {schedule_id} not found")
    return s


async def _validate_schedule_refs(
    session: AsyncSession, *, route_id: int | None, bus_id: int | None, driver_id: int | None
) -> None:
    if route_id is not None:
        route = await md_service.get_route(session, route_id)
        if len(route.stops) < 2:
            raise InvalidState("Route needs at least 2 stops before scheduling", code="route_incomplete")
    if bus_id is not None:
        bus = await md_service.get_bus(session, bus_id)
        if bus.status == BusStatus.RETIRED:
            raise InvalidState(f"Bus {bus.registration_no} is retired", code="bus_retired")
    if driver_id is not None:
        await auth_service.ensure_role(session, driver_id, Role.DRIVER)


async def create_schedule(session: AsyncSession, data: ScheduleIn, *, actor_id: int | None) -> TripSchedule:
    if data.driver_id is None:
        bus = await md_service.get_bus(session, data.bus_id)
        if bus.driver_id is None:
            raise InvalidState(f"Bus {bus.registration_no} has no assigned driver; pick a driver for this schedule",
                               code="driver_required")
        data = data.model_copy(update={"driver_id": bus.driver_id})
    await _validate_schedule_refs(session, route_id=data.route_id, bus_id=data.bus_id, driver_id=data.driver_id)
    s = TripSchedule(**data.model_dump())
    session.add(s)
    await session.flush()
    await events.publish(
        session, "ScheduleCreated",
        {"schedule_id": s.id, "route_id": s.route_id, "bus_id": s.bus_id, "driver_id": s.driver_id},
        aggregate=("schedule", s.id), actor_id=actor_id,
    )
    return s


async def update_schedule(
    session: AsyncSession, schedule_id: int, data: ScheduleUpdate, *, actor_id: int | None
) -> TripSchedule:
    s = await get_schedule(session, schedule_id)
    changes = data.model_dump(exclude_unset=True)
    await _validate_schedule_refs(
        session, route_id=None, bus_id=changes.get("bus_id"), driver_id=changes.get("driver_id")
    )
    if "days_of_week" in changes:
        days = changes["days_of_week"]
        if not days or any(d < 1 or d > 7 for d in days):
            raise InvalidState("days_of_week uses ISO weekdays 1..7", code="bad_days")
        changes["days_of_week"] = sorted(set(days))
    for k, v in changes.items():
        setattr(s, k, v)
    await session.flush()
    trip_ids = await _apply_schedule_to_trips(session, s, changes, actor_id=actor_id)
    await events.publish(session, "ScheduleUpdated",
                         {"schedule_id": s.id, "changes": list(changes), "trip_ids": trip_ids},
                         aggregate=("schedule", s.id), actor_id=actor_id)
    return s


async def _apply_schedule_to_trips(
    session: AsyncSession, s: TripSchedule, changes: dict, *, actor_id: int | None
) -> list[int]:
    """Carry a schedule edit over to its trips that haven't started yet (today onwards), so the
    day's run, its stop times and the delay watcher follow the new schedule.

    Deactivating a schedule only stops future trips being generated: a trip already created
    still runs (that is how one-off runs are made), and an admin cancels it explicitly if not."""
    if not changes.keys() & {"departure_time", "bus_id", "driver_id"}:
        return []
    trips = list(await session.scalars(
        select(Trip).where(Trip.schedule_id == s.id, Trip.status == TripStatus.SCHEDULED,
                           Trip.service_date >= today_local())))
    for t in trips:
        if "departure_time" in changes:
            departure = local_to_utc(t.service_date, s.departure_time)
            shift = departure - t.scheduled_departure
            t.scheduled_departure = departure
            for e in t.stop_events:
                e.scheduled_at += shift
        t.bus_id = s.bus_id
        t.driver_id = s.driver_id
    await session.flush()
    return [t.id for t in trips]


async def reassign_bus_driver(
    session: AsyncSession, bus_id: int, driver_id: int, *, actor_id: int | None = None
) -> dict:
    """Hand a bus's active schedules and its not-yet-started trips (today onwards) to a new driver.
    Called when an admin assigns the bus's driver. Running and finished trips keep their driver."""
    schedules = list(await session.scalars(
        select(TripSchedule).where(TripSchedule.bus_id == bus_id, TripSchedule.is_active.is_(True),
                                   TripSchedule.driver_id != driver_id)))
    trips = list(await session.scalars(
        select(Trip).where(Trip.bus_id == bus_id, Trip.status == TripStatus.SCHEDULED,
                           Trip.service_date >= today_local(), Trip.driver_id != driver_id)))
    for s in schedules:
        s.driver_id = driver_id
    for t in trips:
        t.driver_id = driver_id
    await session.flush()
    summary = {"bus_id": bus_id, "driver_id": driver_id,
               "schedule_ids": [s.id for s in schedules], "trip_ids": [t.id for t in trips]}
    if schedules or trips:
        await events.publish(session, "BusRunsReassigned", summary, aggregate=("bus", bus_id), actor_id=actor_id)
    return summary


# ---------------- Trip generation ----------------
def _stop_plan(route: Route, direction: Direction, departure: datetime) -> list[TripStopEvent]:
    """Build the ordered stop events for one trip. Sequence 1 is the departure point."""
    stops = list(route.stops)
    if direction == Direction.PICKUP:
        ordered = [(rs, rs.offset_min) for rs in stops]
    else:
        total = stops[-1].offset_min
        ordered = [(rs, total - rs.offset_min) for rs in reversed(stops)]
    return [
        TripStopEvent(
            route_stop_id=rs.id,
            stop_id=rs.stop_id,
            stop_name=rs.stop.name,
            sequence=i,
            scheduled_at=departure + timedelta(minutes=minutes),
        )
        for i, (rs, minutes) in enumerate(ordered, start=1)
    ]


async def generate_trips(
    session: AsyncSession, service_date: date | None = None, *, actor_id: int | None = None
) -> GenerateOut:
    """Idempotently create the day's trips from active schedules.

    A schedule whose bus is in maintenance or retired gets no trip (it could never start, and the
    delay watcher would report it all morning). The transport office is told once per day via
    `ScheduleSkipped`; once the bus is back or the schedule gets another bus, the next pass
    creates the trip."""
    if service_date is not None and service_date < today_local():
        raise InvalidState("Trips can't be generated for a past date", code="past_date")
    service_date = service_date or today_local()
    weekday = service_date.isoweekday()
    schedules = list(
        await session.scalars(
            select(TripSchedule).where(
                TripSchedule.is_active.is_(True), TripSchedule.days_of_week.any(weekday)
            )
        )
    )
    existing_ids = set(
        await session.scalars(
            select(Trip.schedule_id).where(Trip.service_date == service_date, Trip.schedule_id.is_not(None))
        )
    )
    buses = {b.id: b for b in await session.scalars(select(Bus).where(Bus.id.in_({s.bus_id for s in schedules})))} \
        if schedules else {}
    created, existing, skipped = 0, 0, 0
    for s in schedules:
        if s.id in existing_ids:
            existing += 1
            continue
        route = await md_service.get_route(session, s.route_id)
        if not route.is_active or len(route.stops) < 2:
            continue
        bus = buses[s.bus_id]
        if bus.status != BusStatus.ACTIVE:
            skipped += 1
            await _report_skipped(session, s, route, bus, service_date, actor_id=actor_id)
            continue
        departure = local_to_utc(service_date, s.departure_time)
        trip = Trip(
            schedule_id=s.id, route_id=s.route_id, bus_id=s.bus_id, driver_id=s.driver_id,
            direction=s.direction, service_date=service_date, scheduled_departure=departure,
            status=TripStatus.SCHEDULED, current_delay_min=0,
            stop_events=_stop_plan(route, s.direction, departure),
        )
        try:
            async with session.begin_nested():
                session.add(trip)
                await session.flush()
            created += 1
        except IntegrityError:  # generated concurrently by another worker/request
            existing += 1
    if created:
        await events.publish(
            session, "TripsGenerated", {"service_date": service_date, "created": created},
            actor_id=actor_id,
        )
    return GenerateOut(service_date=service_date, created=created, existing=existing, skipped=skipped)


async def _report_skipped(
    session: AsyncSession, s: TripSchedule, route: Route, bus: Bus, service_date: date, *, actor_id: int | None
) -> None:
    """Publish ScheduleSkipped once per schedule and day (the generator runs every 15 minutes)."""
    already = await session.scalar(
        select(DomainEvent.id).where(
            DomainEvent.type == "ScheduleSkipped", DomainEvent.aggregate_type == "schedule",
            DomainEvent.aggregate_id == s.id, DomainEvent.payload["service_date"].astext == service_date.isoformat(),
        ).limit(1)
    )
    if already:
        return
    await events.publish(
        session, "ScheduleSkipped",
        {"schedule_id": s.id, "route_id": route.id, "bus_id": bus.id, "registration_no": bus.registration_no,
         "bus_status": bus.status.value, "direction": s.direction.value, "departure_time": s.departure_time,
         "service_date": service_date},
        aggregate=("schedule", s.id), actor_id=actor_id,
    )


# ---------------- Queries ----------------
async def get_trip(session: AsyncSession, trip_id: int) -> Trip:
    trip = await session.get(Trip, trip_id)
    if trip is None:
        raise NotFound(f"Trip {trip_id} not found")
    return trip


async def get_trip_for_update(session: AsyncSession, trip_id: int) -> Trip:
    """The trip, freshly read and locked (SELECT ... FOR UPDATE) until the transaction ends.

    Every state change of a trip (start, arrive, end, cancel, GPS ingest) goes through this, so a
    double tap or a tap racing a GPS arrival runs one after the other and the second sees the
    first's result instead of repeating it."""
    trip = await session.get(Trip, trip_id, with_for_update=True, populate_existing=True)
    if trip is None:
        raise NotFound(f"Trip {trip_id} not found")
    return trip


async def can_view_trip(session: AsyncSession, p: Principal, trip: Trip) -> bool:
    """Admins see every trip, drivers their own, students the trips on their allocated route."""
    if p.role == Role.ADMIN:
        return True
    if p.role == Role.DRIVER:
        return trip.driver_id == p.id
    # Imported here: allocation depends on this module, so a top-level import would be circular.
    from app.modules.allocation import service as alloc_service

    allocation = await alloc_service.get_active(session, p.id)
    return allocation is not None and allocation.route_id == trip.route_id


async def ensure_can_view(session: AsyncSession, p: Principal, trip: Trip) -> None:
    if not await can_view_trip(session, p, trip):
        raise NotFound(f"Trip {trip.id} not found")  # don't reveal other routes' trips exist


async def list_trips(
    session: AsyncSession,
    *,
    service_date: date | None = None,
    status: TripStatus | None = None,
    route_id: int | None = None,
    driver_id: int | None = None,
) -> list[Trip]:
    stmt = select(Trip).order_by(Trip.scheduled_departure)
    if service_date:
        stmt = stmt.where(Trip.service_date == service_date)
    if status:
        stmt = stmt.where(Trip.status == status)
    if route_id:
        stmt = stmt.where(Trip.route_id == route_id)
    if driver_id:
        stmt = stmt.where(Trip.driver_id == driver_id)
    return list(await session.scalars(stmt))


async def active_trips(session: AsyncSession) -> list[Trip]:
    return await list_trips(session, status=TripStatus.IN_PROGRESS)


async def trips_for_route_on(session: AsyncSession, route_id: int, service_date: date) -> list[Trip]:
    return await list_trips(session, route_id=route_id, service_date=service_date)


async def driver_trips(session: AsyncSession, driver_id: int, service_date: date | None = None) -> list[Trip]:
    return await list_trips(session, driver_id=driver_id, service_date=service_date or today_local())


def next_stop(trip: Trip) -> TripStopEvent | None:
    """First stop the bus hasn't reached yet (skipped stops before a reached one don't count)."""
    last_reached = max((e.sequence for e in trip.stop_events if e.arrived_at), default=0)
    return next((e for e in trip.stop_events if e.sequence > last_reached), None)


def stops_after(trip: Trip, sequence: int) -> list[TripStopEvent]:
    return [e for e in trip.stop_events if e.sequence > sequence]


async def route_seat_capacity(session: AsyncSession, route_id: int) -> int | None:
    """Seats every trip on the route can offer = smallest bus among its active schedules.
    None when the route has no active schedule yet (capacity unknown)."""
    return await session.scalar(
        select(func.min(Bus.capacity))
        .join(TripSchedule, TripSchedule.bus_id == Bus.id)
        .where(TripSchedule.route_id == route_id, TripSchedule.is_active.is_(True))
    )


async def route_seat_capacities(session: AsyncSession) -> dict[int, int]:
    """route_seat_capacity for every route that has an active schedule, in one query."""
    rows = await session.execute(
        select(TripSchedule.route_id, func.min(Bus.capacity))
        .join(Bus, Bus.id == TripSchedule.bus_id)
        .where(TripSchedule.is_active.is_(True))
        .group_by(TripSchedule.route_id)
    )
    return {route_id: cap for route_id, cap in rows.all()}


async def trip_details(session: AsyncSession, trips: list[Trip]) -> list[TripDetail]:
    if not trips:
        return []
    routes = {r.id: r for r in await session.scalars(select(Route).where(Route.id.in_({t.route_id for t in trips})))}
    buses = {b.id: b for b in await session.scalars(select(Bus).where(Bus.id.in_({t.bus_id for t in trips})))}
    drivers = await auth_service.get_users(session, [t.driver_id for t in trips])
    out = []
    for t in trips:
        r, b, d = routes[t.route_id], buses[t.bus_id], drivers[t.driver_id]
        ns = next_stop(t) if t.status == TripStatus.IN_PROGRESS else None
        out.append(
            TripDetail(
                **TripOut.model_validate(t).model_dump(),
                route=RouteBrief(id=r.id, code=r.code, name=r.name, color=r.color),
                bus=BusBrief(id=b.id, registration_no=b.registration_no, capacity=b.capacity),
                driver=DriverBrief(id=d.id, full_name=d.full_name, phone=d.phone),
                stops=[StopEventOut.model_validate(e) for e in t.stop_events],
                next_stop=StopEventOut.model_validate(ns) if ns else None,
            )
        )
    return out


async def trip_detail(session: AsyncSession, trip: Trip) -> TripDetail:
    return (await trip_details(session, [trip]))[0]


# ---------------- Driver workflow ----------------
def _ensure_operator(trip: Trip, p: Principal) -> None:
    if p.role == Role.ADMIN:
        return
    if p.role != Role.DRIVER or trip.driver_id != p.id:
        raise Forbidden("Only the assigned driver can operate this trip")


def _effective_time(requested: datetime | None, p: Principal) -> datetime:
    if requested is None:
        return now_utc()
    if not (settings.allow_simulation and p.role == Role.ADMIN):
        raise Forbidden("Explicit timestamps are only allowed for admins with ALLOW_SIMULATION=true "
                        "(development and demos only)", code="simulation_off")
    return requested


def _payload(trip: Trip, **extra) -> dict:
    return {
        "trip_id": trip.id, "route_id": trip.route_id, "bus_id": trip.bus_id,
        "driver_id": trip.driver_id, "direction": trip.direction.value,
        "service_date": trip.service_date, **extra,
    }


async def start_trip(
    session: AsyncSession, trip_id: int, p: Principal, *, started_at: datetime | None = None
) -> Trip:
    trip = await get_trip_for_update(session, trip_id)
    _ensure_operator(trip, p)
    if trip.status != TripStatus.SCHEDULED:
        raise InvalidState(f"Trip is {trip.status.value}, cannot start", code="bad_trip_state")
    if trip.service_date != today_local():
        raise InvalidState(f"This trip runs on {trip.service_date:%d %b}; only today's trips can be started",
                           code="wrong_day")
    bus = await md_service.get_bus(session, trip.bus_id)
    if bus.status != BusStatus.ACTIVE:
        raise InvalidState(f"Bus {bus.registration_no} is {bus.status.value}", code="bus_unavailable")
    await close_stale_trips(session)  # a run left open on an earlier day must not block this one
    busy = await session.scalar(
        select(Trip.id).where(
            Trip.status == TripStatus.IN_PROGRESS,
            (Trip.driver_id == trip.driver_id) | (Trip.bus_id == trip.bus_id),
        )
    )
    if busy:
        raise Conflict(f"Driver or bus already has trip {busy} in progress", code="already_running")

    started = _effective_time(started_at, p)
    delay = minutes_between(started, trip.scheduled_departure)
    trip.status = TripStatus.IN_PROGRESS
    trip.started_at = started
    trip.current_delay_min = max(0, delay)
    first = trip.stop_events[0]  # the bus leaves from stop 1
    first.arrived_at = started
    first.delay_min = delay
    # The partial unique indexes catch a start that raced another one past the check above.
    await flush_or_conflict(session, "Driver or bus already has a trip in progress", code="already_running")
    await events.publish(
        session, "TripStarted",
        _payload(trip, scheduled_departure=trip.scheduled_departure, started_at=started, delay_min=delay),
        aggregate=("trip", trip.id), actor_id=p.id,
    )
    return trip


async def arrive_at_stop(
    session: AsyncSession, trip_id: int, sequence: int, p: Principal, *, arrived_at: datetime | None = None,
    observed_at: datetime | None = None,
) -> Trip:
    """Check the bus in at a stop.

    `arrived_at` is the simulation override (admins only). `observed_at` is for in-process callers
    that detected the arrival themselves (tracking's GPS geofence): the time of the fix, clamped
    between the trip start and now. The router never passes it.
    """
    trip = await get_trip_for_update(session, trip_id)
    _ensure_operator(trip, p)
    if trip.status != TripStatus.IN_PROGRESS:
        raise InvalidState("Trip is not in progress", code="bad_trip_state")
    ev = next((e for e in trip.stop_events if e.sequence == sequence), None)
    if ev is None:
        raise NotFound(f"Trip {trip_id} has no stop #{sequence}")
    if ev.arrived_at is not None:
        raise Conflict(f"Already arrived at {ev.stop_name}", code="already_arrived")
    if any(e.arrived_at for e in trip.stop_events if e.sequence > sequence):
        raise InvalidState("A later stop is already marked as reached", code="out_of_order")

    if observed_at is not None:
        arrived = min(max(observed_at, trip.started_at), now_utc())
    else:
        arrived = _effective_time(arrived_at, p)
    ev.arrived_at = arrived
    ev.delay_min = minutes_between(arrived, ev.scheduled_at)
    trip.current_delay_min = max(0, ev.delay_min)
    await session.flush()
    await events.publish(
        session, "StopArrived",
        _payload(trip, sequence=ev.sequence, stop_id=ev.stop_id, stop_name=ev.stop_name,
                 scheduled_at=ev.scheduled_at, arrived_at=arrived, delay_min=ev.delay_min,
                 is_last=ev.sequence == trip.stop_events[-1].sequence,
                 source="gps" if observed_at is not None else "manual"),
        aggregate=("trip", trip.id), actor_id=p.id,
    )
    return trip


async def end_trip(
    session: AsyncSession, trip_id: int, p: Principal, *, ended_at: datetime | None = None
) -> Trip:
    trip = await get_trip_for_update(session, trip_id)
    _ensure_operator(trip, p)
    if trip.status != TripStatus.IN_PROGRESS:
        raise InvalidState("Trip is not in progress", code="bad_trip_state")
    ended = _effective_time(ended_at, p)
    last = trip.stop_events[-1]  # ending the trip means the bus reached its terminus
    if last.arrived_at is None:
        last.arrived_at = ended
        last.delay_min = minutes_between(ended, last.scheduled_at)
        trip.current_delay_min = max(0, last.delay_min)
    trip.status = TripStatus.COMPLETED
    trip.ended_at = ended
    await session.flush()
    await events.publish(
        session, "TripEnded",
        _payload(trip, ended_at=ended, final_delay_min=last.delay_min,
                 skipped_stops=[e.sequence for e in trip.stop_events if e.arrived_at is None]),
        aggregate=("trip", trip.id), actor_id=p.id,
    )
    return trip


def _last_activity(trip: Trip) -> datetime:
    return max([trip.started_at, *(e.arrived_at for e in trip.stop_events if e.arrived_at)])


async def close_stale_trips(session: AsyncSession) -> list[Trip]:
    """Complete trips left in progress after their service day (the driver never tapped End).

    A trip is stale once its service date is past and it has had no start/stop activity for
    `stale_trip_grace_hours`, so a late run that crosses midnight is left alone. It ends at its
    last activity, and unreached stops (the terminus included) stay unreached rather than
    being marked arrived.
    """
    cutoff = now_utc() - timedelta(hours=settings.stale_trip_grace_hours)
    stale = [t for t in await session.scalars(select(Trip).where(
        Trip.status == TripStatus.IN_PROGRESS, Trip.service_date < today_local()))
        if _last_activity(t) < cutoff]
    for trip in stale:
        ended = _last_activity(trip)
        trip.status = TripStatus.COMPLETED
        trip.ended_at = ended
        await session.flush()
        last = trip.stop_events[-1]
        await events.publish(
            session, "TripEnded",
            _payload(trip, ended_at=ended, final_delay_min=last.delay_min,
                     skipped_stops=[e.sequence for e in trip.stop_events if e.arrived_at is None],
                     auto_closed=True),
            aggregate=("trip", trip.id), actor_id=None,
        )
    return stale


async def cancel_trip(session: AsyncSession, trip_id: int, reason: str, p: Principal) -> Trip:
    trip = await get_trip_for_update(session, trip_id)
    if trip.status not in (TripStatus.SCHEDULED, TripStatus.IN_PROGRESS):
        raise InvalidState(f"Trip is {trip.status.value}, cannot cancel", code="bad_trip_state")
    await _cancel(session, trip, reason, actor_id=p.id)
    return trip


async def _cancel(session: AsyncSession, trip: Trip, reason: str, *, actor_id: int | None) -> None:
    trip.status = TripStatus.CANCELLED
    trip.cancel_reason = reason
    trip.ended_at = now_utc()
    await session.flush()
    # was_running: the bus had set off, so boarding writes attendance for whoever was on it.
    await events.publish(session, "TripCancelled",
                         _payload(trip, reason=reason, was_running=trip.started_at is not None),
                         aggregate=("trip", trip.id), actor_id=actor_id)
