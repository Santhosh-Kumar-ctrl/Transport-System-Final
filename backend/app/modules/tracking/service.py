"""Live bus tracking: GPS ingest, geofenced stop arrival and "bus is near" alerts.

Every position source (today the driver's phone, later Traccar or a simulator) goes through
`ingest()`, so the rules live in one place:

* each fix is stored in `bus_positions`;
* a fix within ARRIVAL_RADIUS_M of one of the next ARRIVAL_LOOKAHEAD_STOPS unreached stops checks
  the bus in there via `trips.service.arrive_at_stop`, so delay detection, notifications,
  dashboard and history react exactly as they do to the driver's ARRIVED button;
* a fix within APPROACH_RADIUS_M of an unreached stop publishes `BusApproaching` once per
  trip and stop. The notifications module picks the riders.

Public API for other modules: ingest, latest_positions, live_trip(s), broadcast.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.config import settings
from app.core.deps import Principal
from app.core.errors import Forbidden, InvalidState
from app.core.realtime import hub
from app.core.roles import Role
from app.core.timeutil import now_utc
from app.modules.master_data.models import Stop
from app.modules.tracking.geo import distance_m
from app.modules.tracking.models import ApproachAlert, BusPosition
from app.modules.tracking.schemas import LiveStop, LiveTrip, PositionIn, PositionOut
from app.modules.trips import service as trips_service
from app.modules.trips.models import Trip, TripStatus, TripStopEvent


@dataclass
class IngestResult:
    trip: Trip
    latest: BusPosition
    accepted: int
    arrived: list[int] = field(default_factory=list)
    approaching: list[int] = field(default_factory=list)


def _ensure_reporter(trip: Trip, p: Principal) -> None:
    if p.role == Role.DRIVER and trip.driver_id == p.id:
        return
    if p.role == Role.ADMIN and settings.allow_simulation:
        return  # replaying a bus in demos
    raise Forbidden("Only the trip's driver can report its position (admins only with ALLOW_SIMULATION=true)")


def _last_reached(trip: Trip) -> int:
    return max((e.sequence for e in trip.stop_events if e.arrived_at), default=0)


def _usable(fix: PositionIn) -> bool:
    return fix.accuracy_m is None or fix.accuracy_m <= settings.max_fix_accuracy_m


async def _stop_coords(session: AsyncSession, stop_ids: set[int]) -> dict[int, tuple[float, float]]:
    """stop_id -> (lat, lng), for stops that have coordinates."""
    if not stop_ids:
        return {}
    rows = await session.execute(select(Stop.id, Stop.latitude, Stop.longitude).where(Stop.id.in_(stop_ids)))
    return {sid: (lat, lng) for sid, lat, lng in rows if lat is not None and lng is not None}


def _arrival_candidate(
    trip: Trip, coords: dict[int, tuple[float, float]], lat: float, lng: float
) -> TripStopEvent | None:
    """The earliest of the next few unreached stops the bus is standing at, if any.

    Looking only a couple of stops ahead stops a road that passes near a much later stop from
    checking the bus in there and skipping everything in between."""
    upcoming = trips_service.stops_after(trip, _last_reached(trip))[: settings.arrival_lookahead_stops]
    for ev in upcoming:
        if ev.stop_id in coords and distance_m(lat, lng, *coords[ev.stop_id]) <= settings.arrival_radius_m:
            return ev
    return None


async def ingest(session: AsyncSession, trip_id: int, fixes: list[PositionIn], p: Principal) -> IngestResult:
    # Serialise ingest per trip (and with taps on Arrived) so two can't check the bus in at the same stop.
    trip = await trips_service.get_trip_for_update(session, trip_id)
    _ensure_reporter(trip, p)
    if trip.status != TripStatus.IN_PROGRESS:
        raise InvalidState("Trip is not in progress", code="bad_trip_state")

    now = now_utc()
    timed = sorted(((min(f.recorded_at or now, now), f) for f in fixes), key=lambda x: x[0])
    coords = await _stop_coords(session, {e.stop_id for e in trip.stop_events})
    alerted = set(await session.scalars(select(ApproachAlert.stop_id).where(ApproachAlert.trip_id == trip.id)))

    rows: list[BusPosition] = []
    result_arrived: list[int] = []
    result_approaching: list[int] = []
    for at, f in timed:
        rows.append(BusPosition(
            trip_id=trip.id, bus_id=trip.bus_id, latitude=f.latitude, longitude=f.longitude,
            speed_kmph=f.speed_kmph, heading_deg=f.heading_deg, accuracy_m=f.accuracy_m, recorded_at=at,
        ))
        if not _usable(f):
            continue

        stop = _arrival_candidate(trip, coords, f.latitude, f.longitude)
        if stop is not None:
            await trips_service.arrive_at_stop(session, trip.id, stop.sequence, p, observed_at=at)
            result_arrived.append(stop.sequence)

        for ev in trips_service.stops_after(trip, _last_reached(trip)):
            if ev.stop_id in alerted or ev.stop_id not in coords:
                continue
            d = distance_m(f.latitude, f.longitude, *coords[ev.stop_id])
            if d > settings.approach_radius_m:
                continue
            alerted.add(ev.stop_id)
            session.add(ApproachAlert(trip_id=trip.id, stop_id=ev.stop_id, sequence=ev.sequence, distance_m=round(d)))
            await events.publish(
                session, "BusApproaching",
                {"trip_id": trip.id, "route_id": trip.route_id, "bus_id": trip.bus_id, "driver_id": trip.driver_id,
                 "direction": trip.direction.value, "sequence": ev.sequence, "stop_id": ev.stop_id,
                 "stop_name": ev.stop_name, "distance_m": round(d), "scheduled_at": ev.scheduled_at,
                 "expected_at": ev.scheduled_at + timedelta(minutes=trip.current_delay_min), "recorded_at": at},
                aggregate=("trip", trip.id), actor_id=p.id,
            )
            result_approaching.append(ev.sequence)

    session.add_all(rows)
    await session.flush()
    return IngestResult(trip=trip, latest=rows[-1], accepted=len(rows),
                        arrived=result_arrived, approaching=result_approaching)


async def broadcast(position: BusPosition, trip: Trip) -> None:
    """Push a fix to everyone watching the route, and to the transport office. Call after commit."""
    data = {**PositionOut.model_validate(position).model_dump(), "route_id": trip.route_id}
    await hub.send_to_topic(f"route:{trip.route_id}", "position", data)
    await hub.send_to_role(Role.ADMIN, "position", data)


async def delete_old_positions(session: AsyncSession, older_than_days: int) -> int:
    """Housekeeping: GPS fixes are kept this long (a bus reports ~720 an hour). Trip history,
    stop arrivals and the domain event log are not affected."""
    cutoff = now_utc() - timedelta(days=older_than_days)
    result = await session.execute(delete(BusPosition).where(BusPosition.recorded_at < cutoff))
    return result.rowcount or 0


# ---------------- Queries ----------------
async def latest_positions(session: AsyncSession, trip_ids: list[int]) -> dict[int, BusPosition]:
    if not trip_ids:
        return {}
    rows = await session.scalars(
        select(BusPosition).where(BusPosition.trip_id.in_(trip_ids))
        .order_by(BusPosition.trip_id, BusPosition.recorded_at.desc(), BusPosition.id.desc())
        .distinct(BusPosition.trip_id)
    )
    return {r.trip_id: r for r in rows}


async def live_trips(session: AsyncSession, trips: list[Trip]) -> list[LiveTrip]:
    details = await trips_service.trip_details(session, trips)
    positions = await latest_positions(session, [t.id for t in trips])
    coords = await _stop_coords(session, {e.stop_id for t in trips for e in t.stop_events})
    out = []
    for t, d in zip(trips, details):
        pos = positions.get(t.id)
        out.append(LiveTrip(
            trip_id=t.id, route=d.route, direction=t.direction, status=t.status,
            bus_registration_no=d.bus.registration_no, delay_min=t.current_delay_min,
            next_stop_sequence=d.next_stop.sequence if d.next_stop else None,
            position=PositionOut.model_validate(pos) if pos else None,
            stops=[LiveStop(sequence=e.sequence, stop_id=e.stop_id, name=e.stop_name,
                            latitude=coords.get(e.stop_id, (None, None))[0],
                            longitude=coords.get(e.stop_id, (None, None))[1],
                            scheduled_at=e.scheduled_at, arrived_at=e.arrived_at)
                   for e in t.stop_events],
        ))
    return out


async def live_trip(session: AsyncSession, trip_id: int) -> LiveTrip:
    return (await live_trips(session, [await trips_service.get_trip(session, trip_id)]))[0]
