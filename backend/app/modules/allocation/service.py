"""Student -> route/stop allocation.

Public API for other modules: get_active, active_on_route, students_at_stops,
count_active_by_route, to_out.
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.deps import Principal
from app.core.errors import Conflict, InvalidState, NotFound
from app.core.roles import Role
from app.core.timeutil import now_utc, today_local
from app.modules.auth import service as auth_service
from app.modules.master_data import service as md_service
from app.modules.master_data.models import Route, Stop
from app.modules.allocation.models import Allocation, AllocationStatus
from app.modules.allocation.schemas import (
    AllocationOut,
    AssignIn,
    AssignResult,
    BulkItemResult,
    RouteRef,
    StopRef,
    StudentRef,
)
from app.modules.trips import service as trips_service


async def get_active(session: AsyncSession, student_id: int) -> Allocation | None:
    return await session.scalar(
        select(Allocation).where(
            Allocation.student_id == student_id, Allocation.status == AllocationStatus.ACTIVE
        )
    )


async def active_on_route(session: AsyncSession, route_id: int) -> list[Allocation]:
    return list(
        await session.scalars(
            select(Allocation).where(
                Allocation.route_id == route_id, Allocation.status == AllocationStatus.ACTIVE
            )
        )
    )


async def students_at_stops(session: AsyncSession, route_id: int, stop_ids: list[int]) -> list[int]:
    if not stop_ids:
        return []
    return list(
        await session.scalars(
            select(Allocation.student_id).where(
                Allocation.route_id == route_id,
                Allocation.stop_id.in_(stop_ids),
                Allocation.status == AllocationStatus.ACTIVE,
            )
        )
    )


async def count_active_by_route(session: AsyncSession) -> dict[int, int]:
    rows = await session.execute(
        select(Allocation.route_id, func.count())
        .where(Allocation.status == AllocationStatus.ACTIVE)
        .group_by(Allocation.route_id)
    )
    return {route_id: n for route_id, n in rows.all()}


async def to_out(session: AsyncSession, allocations: list[Allocation]) -> list[AllocationOut]:
    if not allocations:
        return []
    students = await auth_service.briefs(session, [a.student_id for a in allocations])
    routes = {r.id: r for r in await session.scalars(
        select(Route).where(Route.id.in_({a.route_id for a in allocations})))}
    stops = {s.id: s for s in await session.scalars(
        select(Stop).where(Stop.id.in_({a.stop_id for a in allocations})))}
    out = []
    for a in allocations:
        st, r, sp = students[a.student_id], routes[a.route_id], stops[a.stop_id]
        out.append(
            AllocationOut(
                id=a.id, status=a.status, valid_from=a.valid_from, ended_at=a.ended_at,
                end_reason=a.end_reason, route_stop_id=a.route_stop_id,
                student=StudentRef(id=st.id, full_name=st.full_name, roll_no=st.roll_no),
                route=RouteRef(id=r.id, code=r.code, name=r.name, color=r.color),
                stop=StopRef(id=sp.id, name=sp.name),
            )
        )
    return out


def _end(a: Allocation, reason: str) -> None:
    a.status = AllocationStatus.ENDED
    a.route_stop_id = None
    a.ended_at = now_utc()
    a.end_reason = reason


async def assign(session: AsyncSession, data: AssignIn, *, actor_id: int | None) -> AssignResult:
    await auth_service.ensure_role(session, data.student_id, Role.STUDENT)
    route = await md_service.get_route(session, data.route_id)
    if not route.is_active:
        raise InvalidState(f"Route {route.code} is inactive", code="route_inactive")
    rs = await md_service.find_route_stop(session, data.route_id, data.stop_id)

    current = await get_active(session, data.student_id)
    if current and current.route_id == data.route_id and current.stop_id == data.stop_id:
        return AssignResult(allocation=(await to_out(session, [current]))[0], changed=False)

    # Serialise allocations to the same route so two admins can't both take the last seat.
    await session.execute(select(Route.id).where(Route.id == data.route_id).with_for_update())
    warnings: list[str] = []
    capacity = await trips_service.route_seat_capacity(session, data.route_id)
    if capacity is None:
        warnings.append("Route has no active schedule yet, so seat capacity is unknown")
    else:
        allocated = await session.scalar(
            select(func.count()).select_from(Allocation).where(
                Allocation.route_id == data.route_id,
                Allocation.status == AllocationStatus.ACTIVE,
                Allocation.student_id != data.student_id,
            )
        )
        if allocated + 1 > capacity:
            if not data.force:
                raise Conflict(
                    f"Route {route.code} is full ({allocated}/{capacity} seats allocated)",
                    code="route_full", extra={"allocated": allocated, "capacity": capacity},
                )
            warnings.append(f"Over capacity: {allocated + 1}/{capacity} seats allocated")

    previous = None
    if current:
        previous = {"route_id": current.route_id, "stop_id": current.stop_id}
        _end(current, "reassigned")
        await session.flush()  # free the one-active-per-student slot before inserting

    new = Allocation(
        student_id=data.student_id, route_id=data.route_id, route_stop_id=rs.id,
        stop_id=data.stop_id, status=AllocationStatus.ACTIVE,
        valid_from=data.valid_from or today_local(), created_by=actor_id,
    )
    session.add(new)
    await session.flush()
    payload = {
        "allocation_id": new.id, "student_id": new.student_id,
        "route_id": new.route_id, "stop_id": new.stop_id, "forced": bool(warnings and data.force),
    }
    if previous:
        await events.publish(session, "AllocationChanged", {**payload, "previous": previous},
                             aggregate=("allocation", new.id), actor_id=actor_id)
    else:
        await events.publish(session, "StudentAllocated", payload,
                             aggregate=("allocation", new.id), actor_id=actor_id)
    return AssignResult(allocation=(await to_out(session, [new]))[0], changed=True, warnings=warnings)


async def bulk_assign(
    session: AsyncSession, items: list[AssignIn], *, actor_id: int | None
) -> list[BulkItemResult]:
    """Each item runs in its own savepoint: one bad row doesn't sink the batch."""
    results = []
    for item in items:
        try:
            async with session.begin_nested():
                res = await assign(session, item, actor_id=actor_id)
            results.append(BulkItemResult(student_id=item.student_id, ok=True, result=res))
        except (Conflict, InvalidState, NotFound) as exc:
            results.append(BulkItemResult(student_id=item.student_id, ok=False, error=exc.message))
    return results


async def unassign(session: AsyncSession, student_id: int, *, actor_id: int | None,
                   reason: str = "unassigned") -> None:
    current = await get_active(session, student_id)
    if current is None:
        raise NotFound("Student has no active allocation")
    route_id, stop_id = current.route_id, current.stop_id
    _end(current, reason)
    await session.flush()
    await events.publish(
        session, "AllocationEnded",
        {"allocation_id": current.id, "student_id": student_id, "route_id": route_id, "stop_id": stop_id},
        aggregate=("allocation", current.id), actor_id=actor_id,
    )


async def list_allocations(
    session: AsyncSession,
    *,
    route_id: int | None = None,
    stop_id: int | None = None,
    student_id: int | None = None,
    status: AllocationStatus | None = AllocationStatus.ACTIVE,
) -> list[Allocation]:
    stmt = select(Allocation).order_by(Allocation.route_id, Allocation.stop_id, Allocation.id)
    if route_id:
        stmt = stmt.where(Allocation.route_id == route_id)
    if stop_id:
        stmt = stmt.where(Allocation.stop_id == stop_id)
    if student_id:
        stmt = stmt.where(Allocation.student_id == student_id)
    if status:
        stmt = stmt.where(Allocation.status == status)
    return list(await session.scalars(stmt))


async def end_for_deactivated(session: AsyncSession, student_id: int, *, actor_id: int | None) -> bool:
    """A deactivated student gives their seat back. Returns whether they had one."""
    if await get_active(session, student_id) is None:
        return False
    await unassign(session, student_id, actor_id=actor_id, reason="deactivated")
    return True


async def may_follow_route(session: AsyncSession, p: Principal, route_id: int) -> bool:
    """WebSocket "route:<id>" topics: the route's allocated students and today's drivers on it."""
    if p.role == Role.ADMIN:
        return True
    if p.role == Role.STUDENT:
        a = await get_active(session, p.id)
        return a is not None and a.route_id == route_id
    if p.role == Role.DRIVER:
        return any(t.route_id == route_id for t in await trips_service.driver_trips(session, p.id))
    return False


async def allocation_valid_on(session: AsyncSession, student_id: int, route_id: int, on: date) -> bool:
    a = await get_active(session, student_id)
    return bool(a and a.route_id == route_id and a.valid_from <= on)
