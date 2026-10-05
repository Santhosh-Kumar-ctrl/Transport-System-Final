from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.access import authorize_trip
from app.core.deps import Principal, current_principal, require_roles
from app.core.roles import Role
from app.modules.tracking import service
from app.modules.tracking.schemas import IngestOut, LiveTrip, PositionBatchIn, PositionOut
from app.modules.trips import service as trips_service

router = APIRouter(tags=["tracking"])


@router.post("/trips/{trip_id}/positions", response_model=IngestOut)
async def report_positions(trip_id: int, body: PositionBatchIn,
                           p: Principal = Depends(require_roles(Role.DRIVER, Role.ADMIN)),
                           session: AsyncSession = Depends(get_session)):
    result = await service.ingest(session, trip_id, body.positions, p)
    await session.commit()
    await service.broadcast(result.latest, result.trip)
    return IngestOut(accepted=result.accepted, arrived=result.arrived, approaching=result.approaching,
                     position=PositionOut.model_validate(result.latest))


@router.get("/trips/{trip_id}/live", response_model=LiveTrip)
async def trip_live(trip_id: int, p: Principal = Depends(current_principal),
                    session: AsyncSession = Depends(get_session)):
    await trips_service.ensure_can_view(session, p, await trips_service.get_trip(session, trip_id))
    return await service.live_trip(session, trip_id)


@router.get("/tracking/live", response_model=list[LiveTrip])
async def all_live(_: Principal = Depends(require_roles(Role.ADMIN)),
                   session: AsyncSession = Depends(get_session)):
    return await service.live_trips(session, await trips_service.active_trips(session))
