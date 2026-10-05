"""Master data: buses, stops, routes and the ordered stops of each route.

Public API for other modules: get_bus, get_route, get_route_stop, list_routes.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.db import flush_or_conflict
from app.core.errors import Conflict, InvalidState, NotFound
from app.core.roles import Role
from app.modules.auth import service as auth_service
from app.modules.master_data.models import Bus, Route, RouteStop, Stop
from app.modules.master_data.schemas import (
    BusIn,
    BusUpdate,
    RouteIn,
    RouteStopIn,
    RouteUpdate,
    StopIn,
    StopUpdate,
)


# ---------------- Buses ----------------
async def list_buses(session: AsyncSession) -> list[Bus]:
    return list(await session.scalars(select(Bus).order_by(Bus.registration_no)))


async def get_bus(session: AsyncSession, bus_id: int) -> Bus:
    bus = await session.get(Bus, bus_id)
    if bus is None:
        raise NotFound(f"Bus {bus_id} not found")
    return bus


async def create_bus(session: AsyncSession, data: BusIn, *, actor_id: int | None) -> Bus:
    bus = Bus(**data.model_dump())
    session.add(bus)
    await flush_or_conflict(session, f"Bus {data.registration_no} already exists")
    await session.refresh(bus, ["driver"])  # load now; lazy-loading later isn't async-safe
    await events.publish(session, "BusCreated", {"bus_id": bus.id, "registration_no": bus.registration_no},
                         aggregate=("bus", bus.id), actor_id=actor_id)
    return bus


async def update_bus(session: AsyncSession, bus_id: int, data: BusUpdate, *, actor_id: int | None) -> Bus:
    bus = await get_bus(session, bus_id)
    old_status = bus.status
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(bus, k, v.strip().upper() if k == "registration_no" else v)
    await flush_or_conflict(session, "Registration number already in use")
    if bus.status != old_status:
        await events.publish(
            session, "BusStatusChanged",
            {"bus_id": bus.id, "registration_no": bus.registration_no,
             "from": old_status.value, "to": bus.status.value},
            aggregate=("bus", bus.id), actor_id=actor_id,
        )
    return bus


async def assign_driver(
    session: AsyncSession, bus_id: int, driver_id: int | None, *, move: bool = False, actor_id: int | None
) -> Bus:
    """Set (or clear) a bus's regular driver. The trips module reacts to `BusDriverAssigned`
    by handing the bus's active schedules and not-yet-started trips to the new driver."""
    bus = await get_bus(session, bus_id)
    previous = bus.driver_id
    if driver_id == previous:
        return bus

    moved_from: Bus | None = None
    if driver_id is not None:
        await auth_service.ensure_role(session, driver_id, Role.DRIVER)
        other = await session.scalar(select(Bus).where(Bus.driver_id == driver_id, Bus.id != bus.id))
        if other is not None:
            if not move:
                raise Conflict(
                    f"This driver already drives bus {other.registration_no}",
                    code="driver_taken",
                    extra={"bus_id": other.id, "registration_no": other.registration_no},
                )
            other.driver_id = None
            moved_from = other
            await session.flush()  # free the unique slot before taking it

    bus.driver_id = driver_id
    await session.flush()
    await session.refresh(bus, ["driver"])
    await events.publish(
        session, "BusDriverAssigned",
        {"bus_id": bus.id, "registration_no": bus.registration_no, "driver_id": driver_id,
         "previous_driver_id": previous,
         "moved_from_bus_id": moved_from.id if moved_from else None,
         "moved_from_registration_no": moved_from.registration_no if moved_from else None},
        aggregate=("bus", bus.id), actor_id=actor_id,
    )
    return bus


async def delete_bus(session: AsyncSession, bus_id: int) -> None:
    bus = await get_bus(session, bus_id)
    await session.delete(bus)
    await flush_or_conflict(session, "Bus is referenced by schedules or trips; retire it instead")


# ---------------- Stops ----------------
async def list_stops(session: AsyncSession, q: str | None = None) -> list[Stop]:
    stmt = select(Stop).order_by(Stop.name)
    if q:
        stmt = stmt.where(Stop.name.ilike(f"%{q}%"))
    return list(await session.scalars(stmt))


async def get_stop(session: AsyncSession, stop_id: int) -> Stop:
    stop = await session.get(Stop, stop_id)
    if stop is None:
        raise NotFound(f"Stop {stop_id} not found")
    return stop


async def create_stop(session: AsyncSession, data: StopIn) -> Stop:
    stop = Stop(**data.model_dump())
    session.add(stop)
    await session.flush()
    return stop


async def update_stop(session: AsyncSession, stop_id: int, data: StopUpdate) -> Stop:
    stop = await get_stop(session, stop_id)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(stop, k, v)
    await flush_or_conflict(session, "Couldn't save the stop")
    return stop


async def delete_stop(session: AsyncSession, stop_id: int) -> None:
    stop = await get_stop(session, stop_id)
    await session.delete(stop)
    await flush_or_conflict(session, "Stop is used by a route; remove it from routes first")


# ---------------- Routes ----------------
async def list_routes(session: AsyncSession, *, active_only: bool = False) -> list[Route]:
    stmt = select(Route).order_by(Route.code)
    if active_only:
        stmt = stmt.where(Route.is_active.is_(True))
    return list(await session.scalars(stmt))


async def get_route(session: AsyncSession, route_id: int) -> Route:
    route = await session.get(Route, route_id)
    if route is None:
        raise NotFound(f"Route {route_id} not found")
    return route


async def get_route_stop(session: AsyncSession, route_stop_id: int) -> RouteStop:
    rs = await session.get(RouteStop, route_stop_id)
    if rs is None:
        raise NotFound(f"Route stop {route_stop_id} not found")
    return rs


async def find_route_stop(session: AsyncSession, route_id: int, stop_id: int) -> RouteStop:
    rs = await session.scalar(
        select(RouteStop).where(RouteStop.route_id == route_id, RouteStop.stop_id == stop_id)
    )
    if rs is None:
        raise InvalidState(f"Stop {stop_id} is not on route {route_id}", code="stop_not_on_route")
    return rs


async def create_route(session: AsyncSession, data: RouteIn, *, actor_id: int | None) -> Route:
    route = Route(**data.model_dump(), stops=[])
    session.add(route)
    await flush_or_conflict(session, f"Route code {data.code} already exists")
    await events.publish(session, "RouteCreated", {"route_id": route.id, "code": route.code},
                         aggregate=("route", route.id), actor_id=actor_id)
    return route


async def update_route(
    session: AsyncSession, route_id: int, data: RouteUpdate, *, actor_id: int | None
) -> Route:
    route = await get_route(session, route_id)
    changes = data.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(route, k, v)
    await flush_or_conflict(session, "Route code already in use")
    await events.publish(session, "RouteUpdated", {"route_id": route.id, "changes": list(changes)},
                         aggregate=("route", route.id), actor_id=actor_id)
    return route


async def set_route_stops(
    session: AsyncSession, route_id: int, items: list[RouteStopIn], *, actor_id: int | None
) -> Route:
    """Replace the ordered stop list. Existing RouteStop rows are kept (and re-sequenced)
    for stops still present, so student allocations pointing at them survive a reorder.
    Removing a stop that students are allocated to is refused (FK RESTRICT)."""
    route = await get_route(session, route_id)
    stop_ids = [i.stop_id for i in items]
    if len(set(stop_ids)) != len(stop_ids):
        raise InvalidState("A stop can appear only once on a route", code="duplicate_stop")
    offsets = [i.offset_min for i in items]
    if offsets != sorted(offsets):
        raise InvalidState("offset_min must not decrease along the route", code="bad_offsets")
    stops = {s.id: s for s in await session.scalars(select(Stop).where(Stop.id.in_(stop_ids)))} if items else {}
    missing = set(stop_ids) - set(stops)
    if missing:
        raise NotFound(f"Stops not found: {sorted(missing)}")

    existing = {rs.stop_id: rs for rs in route.stops}
    new_list: list[RouteStop] = []
    for seq, item in enumerate(items, start=1):
        # Attach the Stop object itself so `rs.stop` is loaded (no async lazy-load later).
        rs = existing.pop(item.stop_id, None) or RouteStop(stop_id=item.stop_id, stop=stops[item.stop_id])
        rs.sequence = seq
        rs.offset_min = item.offset_min
        new_list.append(rs)
    route.stops = new_list  # delete-orphan removes whatever is left in `existing`
    await flush_or_conflict(
        session, "Cannot remove a stop that students are allocated to; reassign them first"
    )
    await events.publish(
        session, "RouteStopsChanged",
        {"route_id": route.id, "stop_ids": stop_ids, "removed_stop_ids": list(existing)},
        aggregate=("route", route.id), actor_id=actor_id,
    )
    return route
