from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, current_principal, require_roles
from app.core.roles import Role
from app.modules.master_data import service
from app.modules.master_data.schemas import (
    AssignDriverIn,
    BusIn,
    BusOut,
    BusUpdate,
    RouteDetail,
    RouteIn,
    RouteOut,
    RouteStopIn,
    RouteUpdate,
    StopIn,
    StopOut,
    StopUpdate,
)

router = APIRouter(tags=["master-data"])
admin_only = require_roles(Role.ADMIN)


# ---- Buses (reads: admin + driver) ----
@router.get("/buses", response_model=list[BusOut])
async def list_buses(_: Principal = Depends(require_roles(Role.ADMIN, Role.DRIVER)),
                     session: AsyncSession = Depends(get_session)):
    return await service.list_buses(session)


@router.post("/buses", response_model=BusOut, status_code=201)
async def create_bus(body: BusIn, p: Principal = Depends(admin_only),
                     session: AsyncSession = Depends(get_session)):
    bus = await service.create_bus(session, body, actor_id=p.id)
    await session.commit()
    return bus


@router.get("/buses/{bus_id}", response_model=BusOut)
async def get_bus(bus_id: int, _: Principal = Depends(require_roles(Role.ADMIN, Role.DRIVER)),
                  session: AsyncSession = Depends(get_session)):
    return await service.get_bus(session, bus_id)


@router.patch("/buses/{bus_id}", response_model=BusOut)
async def update_bus(bus_id: int, body: BusUpdate, p: Principal = Depends(admin_only),
                     session: AsyncSession = Depends(get_session)):
    bus = await service.update_bus(session, bus_id, body, actor_id=p.id)
    await session.commit()
    return bus


@router.put("/buses/{bus_id}/driver", response_model=BusOut)
async def assign_driver(bus_id: int, body: AssignDriverIn, p: Principal = Depends(admin_only),
                        session: AsyncSession = Depends(get_session)):
    """Assign the bus's regular driver (null removes). 409 `driver_taken` if they drive another
    bus, unless `move: true`."""
    bus = await service.assign_driver(session, bus_id, body.driver_id, move=body.move, actor_id=p.id)
    await session.commit()
    return bus


@router.delete("/buses/{bus_id}", status_code=204)
async def delete_bus(bus_id: int, _: Principal = Depends(admin_only),
                     session: AsyncSession = Depends(get_session)):
    await service.delete_bus(session, bus_id)
    await session.commit()
    return Response(status_code=204)


# ---- Stops (reads: any signed-in user) ----
@router.get("/stops", response_model=list[StopOut])
async def list_stops(q: str | None = None, _: Principal = Depends(current_principal),
                     session: AsyncSession = Depends(get_session)):
    return await service.list_stops(session, q)


@router.post("/stops", response_model=StopOut, status_code=201)
async def create_stop(body: StopIn, _: Principal = Depends(admin_only),
                      session: AsyncSession = Depends(get_session)):
    stop = await service.create_stop(session, body)
    await session.commit()
    return stop


@router.patch("/stops/{stop_id}", response_model=StopOut)
async def update_stop(stop_id: int, body: StopUpdate, _: Principal = Depends(admin_only),
                      session: AsyncSession = Depends(get_session)):
    stop = await service.update_stop(session, stop_id, body)
    await session.commit()
    return stop


@router.delete("/stops/{stop_id}", status_code=204)
async def delete_stop(stop_id: int, _: Principal = Depends(admin_only),
                      session: AsyncSession = Depends(get_session)):
    await service.delete_stop(session, stop_id)
    await session.commit()
    return Response(status_code=204)


# ---- Routes (reads: any signed-in user) ----
@router.get("/routes", response_model=list[RouteDetail])
async def list_routes(active_only: bool = False, _: Principal = Depends(current_principal),
                      session: AsyncSession = Depends(get_session)):
    return await service.list_routes(session, active_only=active_only)


@router.post("/routes", response_model=RouteDetail, status_code=201)
async def create_route(body: RouteIn, p: Principal = Depends(admin_only),
                       session: AsyncSession = Depends(get_session)):
    route = await service.create_route(session, body, actor_id=p.id)
    await session.commit()
    return route


@router.get("/routes/{route_id}", response_model=RouteDetail)
async def get_route(route_id: int, _: Principal = Depends(current_principal),
                    session: AsyncSession = Depends(get_session)):
    return await service.get_route(session, route_id)


@router.patch("/routes/{route_id}", response_model=RouteOut)
async def update_route(route_id: int, body: RouteUpdate, p: Principal = Depends(admin_only),
                       session: AsyncSession = Depends(get_session)):
    route = await service.update_route(session, route_id, body, actor_id=p.id)
    await session.commit()
    return route


@router.put("/routes/{route_id}/stops", response_model=RouteDetail)
async def set_route_stops(route_id: int, body: list[RouteStopIn], p: Principal = Depends(admin_only),
                          session: AsyncSession = Depends(get_session)):
    route = await service.set_route_stops(session, route_id, body, actor_id=p.id)
    await session.commit()
    return route
