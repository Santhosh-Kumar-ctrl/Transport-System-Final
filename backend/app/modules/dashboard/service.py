"""Per-role dashboard aggregates. Owns no tables: composes other modules' services."""

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.timeutil import today_local
from app.modules.allocation import service as alloc_service
from app.modules.allocation.schemas import MyAllocation
from app.modules.boarding import service as boarding_service
from app.modules.capacity import service as capacity_service
from app.modules.dashboard.schemas import (
    AdminDashboard,
    BoardRow,
    DriverDashboard,
    DriverTrip,
    MyStopView,
    NextStop,
    StudentDashboard,
    StudentTrip,
    TripCounts,
)
from app.modules.delay_monitor import service as delay_service
from app.modules.history import service as history_service
from app.modules.master_data import service as md_service
from app.modules.master_data.schemas import RouteDetail
from app.modules.notifications import service as notif_service
from app.modules.trips import service as trips_service
from app.modules.trips.models import Trip, TripStatus
from app.modules.trips.schemas import TripDetail

ALERT_EVENTS = ["TripDelayed", "TripDelayResolved", "OverCapacity", "CapacityWarning",
                "UnallocatedBoarding", "TripCancelled", "ScheduleSkipped"]


async def _delays(session: AsyncSession, trips: list[Trip]) -> dict[int, int]:
    """Effective delay per trip: the worse of the last check-in and the last alert."""
    alerts = await delay_service.current_delays(session, [t.id for t in trips])
    return {
        t.id: (max(t.current_delay_min, alerts.get(t.id, 0)) if t.status in (TripStatus.IN_PROGRESS, TripStatus.SCHEDULED)
               else t.current_delay_min)
        for t in trips
    }


def _stops_done(d: TripDetail) -> int:
    return sum(1 for s in d.stops if s.arrived_at)


async def admin(session: AsyncSession) -> AdminDashboard:
    today = today_local()
    trips = await trips_service.list_trips(session, service_date=today)
    details = {d.id: d for d in await trips_service.trip_details(session, trips)}
    delays = await _delays(session, trips)
    allocated = await alloc_service.count_active_by_route(session)
    occupancy = await capacity_service.trip_occupancies(session, trips)
    counts = TripCounts()
    board = []
    for t in trips:
        d = details[t.id]
        setattr(counts, t.status.value, getattr(counts, t.status.value) + 1)
        delay = delays[t.id]
        live = t.status in (TripStatus.IN_PROGRESS, TripStatus.SCHEDULED)
        if live and delay >= settings.delay_threshold_min:
            counts.delayed_now += 1
        occ = occupancy[t.id]
        ns = d.next_stop
        board.append(BoardRow(
            trip_id=t.id, route=d.route, direction=t.direction, bus_registration_no=d.bus.registration_no,
            driver_name=d.driver.full_name, status=t.status, scheduled_departure=t.scheduled_departure,
            started_at=t.started_at, delay_min=delay,
            next_stop=NextStop(sequence=ns.sequence, name=ns.stop_name, scheduled_at=ns.scheduled_at,
                               expected_at=ns.scheduled_at + timedelta(minutes=delay)) if ns else None,
            stops_total=len(d.stops), stops_done=_stops_done(d), boarded=occ.boarded, capacity=occ.capacity,
            occupancy_level=occ.level, allocated=allocated.get(t.route_id, 0),
        ))
    # Running trips first, then upcoming, then finished; each by departure time.
    order = {TripStatus.IN_PROGRESS: 0, TripStatus.SCHEDULED: 1, TripStatus.COMPLETED: 2, TripStatus.CANCELLED: 3}
    board.sort(key=lambda r: (order[r.status], r.scheduled_departure))
    alerts = await history_service.events(session, types=ALERT_EVENTS, date_from=today, date_to=today, limit=25)
    return AdminDashboard(service_date=today, counts=counts, board=board, alerts=alerts,
                          utilization=await capacity_service.route_utilization(session))


async def driver(session: AsyncSession, driver_id: int) -> DriverDashboard:
    today = today_local()
    trips = await trips_service.driver_trips(session, driver_id, today)
    details = await trips_service.trip_details(session, trips)
    delays = await _delays(session, trips)
    occupancy = await capacity_service.trip_occupancies(session, trips)
    out = []
    for t, d in zip(trips, details):
        occ = occupancy[t.id]
        out.append(DriverTrip(trip=d, delay_min=delays[t.id], boarded=occ.boarded, capacity=occ.capacity,
                              occupancy_level=occ.level))
    active = next((x for x in out if x.trip.status == TripStatus.IN_PROGRESS), None)
    return DriverDashboard(service_date=today, active=active, trips=out)


async def student(session: AsyncSession, student_id: int) -> StudentDashboard:
    today = today_local()
    unread = await notif_service.unread_count(session, student_id)
    alloc = await alloc_service.get_active(session, student_id)
    if alloc is None:
        return StudentDashboard(service_date=today, allocation=None, trips=[], unread_notifications=unread)

    route = await md_service.get_route(session, alloc.route_id)
    my_alloc = MyAllocation(allocation=(await alloc_service.to_out(session, [alloc]))[0],
                            route=RouteDetail.model_validate(route))
    trips = await trips_service.trips_for_route_on(session, alloc.route_id, today)
    details = await trips_service.trip_details(session, trips)
    delays = await _delays(session, trips)
    boardings = await boarding_service.student_boardings_on(session, student_id, [t.id for t in trips])
    views = []
    for t, d in zip(trips, details):
        delay = delays[t.id]
        mine = next((s for s in d.stops if s.stop_id == alloc.stop_id), None)
        b = boardings.get(t.id)
        views.append(StudentTrip(
            trip_id=t.id, direction=t.direction, status=t.status, scheduled_departure=t.scheduled_departure,
            started_at=t.started_at, bus_registration_no=d.bus.registration_no, delay_min=delay,
            my_stop=MyStopView(
                stop_id=mine.stop_id, name=mine.stop_name, sequence=mine.sequence, scheduled_at=mine.scheduled_at,
                expected_at=mine.arrived_at or (mine.scheduled_at + timedelta(minutes=delay)),
                arrived_at=mine.arrived_at,
            ) if mine else None,
            next_stop_name=d.next_stop.stop_name if d.next_stop else None,
            stops_done=_stops_done(d), stops_total=len(d.stops),
            boarded=b is not None, boarded_at=b.boarded_at if b else None,
        ))
    return StudentDashboard(service_date=today, allocation=my_alloc, trips=views, unread_notifications=unread)
