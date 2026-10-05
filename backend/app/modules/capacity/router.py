from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, current_principal, require_roles
from app.core.roles import Role
from app.modules.capacity import service
from app.modules.capacity.schemas import RouteUtilization, TripOccupancy
from app.modules.trips import service as trips_service

router = APIRouter(prefix="/capacity", tags=["capacity"])
admin_only = require_roles(Role.ADMIN)


@router.get("/trips/{trip_id}", response_model=TripOccupancy)
async def trip_occupancy(trip_id: int, p: Principal = Depends(current_principal),
                         session: AsyncSession = Depends(get_session)):
    trip = await trips_service.get_trip(session, trip_id)
    await trips_service.ensure_can_view(session, p, trip)
    return await service.trip_occupancy(session, trip)


@router.get("/active", response_model=list[TripOccupancy])
async def active(_: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    return await service.active_occupancy(session)


@router.get("/routes", response_model=list[RouteUtilization])
async def routes(_: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    return await service.route_utilization(session)
