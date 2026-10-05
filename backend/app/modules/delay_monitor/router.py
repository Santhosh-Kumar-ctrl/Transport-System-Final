from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, current_principal, require_roles
from app.core.roles import Role
from app.modules.delay_monitor import service
from app.modules.delay_monitor.schemas import DelayReportOut, ReportDelayIn
from app.modules.trips import service as trips_service

router = APIRouter(tags=["delays"])


@router.post("/trips/{trip_id}/delay", response_model=DelayReportOut, status_code=201)
async def report_delay(trip_id: int, body: ReportDelayIn,
                       p: Principal = Depends(require_roles(Role.DRIVER, Role.ADMIN)),
                       session: AsyncSession = Depends(get_session)):
    report = await service.report_manual(session, trip_id, p, body.delay_min, body.reason)
    await session.commit()
    return report


@router.get("/trips/{trip_id}/delays", response_model=list[DelayReportOut])
async def trip_delays(trip_id: int, p: Principal = Depends(current_principal),
                      session: AsyncSession = Depends(get_session)):
    await trips_service.ensure_can_view(session, p, await trips_service.get_trip(session, trip_id))
    return await service.list_reports(session, trip_id=trip_id)


@router.get("/delays", response_model=list[DelayReportOut])
async def delays(service_date: date | None = None,
                 _: Principal = Depends(require_roles(Role.ADMIN)),
                 session: AsyncSession = Depends(get_session)):
    return await service.list_reports(session, service_date=service_date)


@router.post("/delays/watch", response_model=dict)
async def run_watcher(_: Principal = Depends(require_roles(Role.ADMIN)),
                      session: AsyncSession = Depends(get_session)):
    """Run one watcher pass now (the background job does this every DELAY_WATCH_INTERVAL_SECONDS)."""
    raised = await service.watch_once(session)
    await session.commit()
    return {"raised": raised}
