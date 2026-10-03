from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.access import authorize_trip
from app.core.deps import Principal, current_principal, require_roles
from app.core.roles import Role
from app.modules.trips import service
from app.modules.trips.models import TripStatus
from app.modules.trips.schemas import (
    ArriveIn,
    CancelIn,
    GenerateIn,
    GenerateOut,
    ScheduleIn,
    ScheduleOut,
    ScheduleUpdate,
    StartIn,
    TripDetail,
)

router = APIRouter(tags=["trips"])
admin_only = require_roles(Role.ADMIN)
operator = require_roles(Role.ADMIN, Role.DRIVER)


# ---- Schedules ----
@router.get("/schedules", response_model=list[ScheduleOut])
async def list_schedules(route_id: int | None = None, driver_id: int | None = None,
                         _: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    return await service.list_schedules(session, route_id=route_id, driver_id=driver_id)


@router.post("/schedules", response_model=ScheduleOut, status_code=201)
async def create_schedule(body: ScheduleIn, p: Principal = Depends(admin_only),
                          session: AsyncSession = Depends(get_session)):
    s = await service.create_schedule(session, body, actor_id=p.id)
    await session.commit()
    return s


@router.patch("/schedules/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(schedule_id: int, body: ScheduleUpdate, p: Principal = Depends(admin_only),
                          session: AsyncSession = Depends(get_session)):
    s = await service.update_schedule(session, schedule_id, body, actor_id=p.id)
    await session.commit()
    return s


# ---- Trips ----
@router.post("/trips/generate", response_model=GenerateOut)
async def generate(body: GenerateIn, p: Principal = Depends(admin_only),
                   session: AsyncSession = Depends(get_session)):
    out = await service.generate_trips(session, body.service_date, actor_id=p.id)
    await session.commit()
    return out


@router.get("/trips", response_model=list[TripDetail])
async def list_trips(service_date: date | None = None, status: TripStatus | None = None,
                     route_id: int | None = None, driver_id: int | None = None,
                     _: Principal = Depends(require_roles(Role.ADMIN)),
                     session: AsyncSession = Depends(get_session)):
    trips = await service.list_trips(session, service_date=service_date, status=status,
                                     route_id=route_id, driver_id=driver_id)
    return await service.trip_details(session, trips)


@router.get("/trips/mine", response_model=list[TripDetail])
async def my_trips(service_date: date | None = None, p: Principal = Depends(require_roles(Role.DRIVER)),
                   session: AsyncSession = Depends(get_session)):
    return await service.trip_details(session, await service.driver_trips(session, p.id, service_date))


@router.get("/trips/{trip_id}", response_model=TripDetail)
async def get_trip(trip_id: int, p: Principal = Depends(current_principal),
                   session: AsyncSession = Depends(get_session)):
    return await service.trip_detail(session, await authorize_trip(session, p, trip_id))


@router.post("/trips/{trip_id}/start", response_model=TripDetail)
async def start_trip(trip_id: int, body: StartIn | None = None, p: Principal = Depends(operator),
                     session: AsyncSession = Depends(get_session)):
    trip = await service.start_trip(session, trip_id, p, started_at=body.started_at if body else None)
    await session.commit()
    return await service.trip_detail(session, trip)


@router.post("/trips/{trip_id}/stops/{sequence}/arrive", response_model=TripDetail)
async def arrive(trip_id: int, sequence: int, body: ArriveIn | None = None,
                 p: Principal = Depends(operator), session: AsyncSession = Depends(get_session)):
    trip = await service.arrive_at_stop(session, trip_id, sequence, p,
                                        arrived_at=body.arrived_at if body else None)
    await session.commit()
    return await service.trip_detail(session, trip)


@router.post("/trips/{trip_id}/end", response_model=TripDetail)
async def end_trip(trip_id: int, p: Principal = Depends(operator),
                   session: AsyncSession = Depends(get_session)):
    trip = await service.end_trip(session, trip_id, p)
    await session.commit()
    return await service.trip_detail(session, trip)


@router.post("/trips/{trip_id}/cancel", response_model=TripDetail)
async def cancel_trip(trip_id: int, body: CancelIn, p: Principal = Depends(admin_only),
                      session: AsyncSession = Depends(get_session)):
    trip = await service.cancel_trip(session, trip_id, body.reason, p)
    await session.commit()
    return await service.trip_detail(session, trip)
