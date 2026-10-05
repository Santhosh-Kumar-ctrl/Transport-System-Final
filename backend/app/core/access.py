"""Object authorization shared by live HTTP reads and WebSocket topics."""
from sqlalchemy import select
from app.core.deps import Principal
from app.core.errors import Forbidden, NotFound
from app.core.roles import Role


async def can_view_route(session, p: Principal, route_id: int) -> bool:
    from app.modules.allocation.models import Allocation, AllocationStatus
    from app.modules.trips.models import Trip
    if p.role == Role.ADMIN:
        return True
    if p.role == Role.DRIVER:
        return bool(await session.scalar(select(Trip.id).where(
            Trip.driver_id == p.id, Trip.route_id == route_id).limit(1)))
    return bool(await session.scalar(select(Allocation.id).where(
        Allocation.student_id == p.id, Allocation.route_id == route_id,
        Allocation.status == AllocationStatus.ACTIVE).limit(1)))


async def authorize_trip(session, p: Principal, trip_id: int):
    from app.modules.trips.models import Trip
    trip = await session.get(Trip, trip_id)
    if trip is None:
        raise NotFound("Trip not found")
    permitted = (p.role == Role.ADMIN or
                 (p.role == Role.DRIVER and trip.driver_id == p.id) or
                 (p.role == Role.STUDENT and await can_view_route(session, p, trip.route_id)))
    if not permitted:
        raise Forbidden("Trip is not assigned to you")
    return trip


async def can_subscribe(session, p: Principal, topic: str) -> bool:
    from app.modules.trips.models import Trip
    kind, sep, raw_id = topic.partition(":")
    if not sep or not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 10:
        return False
    object_id = int(raw_id)
    if object_id <= 0:
        return False
    if kind == "route":
        return await can_view_route(session, p, object_id)
    if kind == "trip":
        # Boarding broadcasts contain students' names and IDs, not rider-facing data.
        trip = await session.get(Trip, object_id)
        return trip is not None and (p.role == Role.ADMIN or
                                    (p.role == Role.DRIVER and trip.driver_id == p.id))
    return False
