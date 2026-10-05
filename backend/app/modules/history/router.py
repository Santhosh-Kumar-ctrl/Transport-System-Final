from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, require_roles
from app.core.roles import Role
from app.modules.history import service
from app.modules.history.schemas import AttendanceRow, EventOut, TripReport

router = APIRouter(prefix="/history", tags=["history"])
admin_only = require_roles(Role.ADMIN)


@router.get("/events", response_model=list[EventOut])
async def events(type: list[str] | None = Query(None), aggregate_type: str | None = None,
                 aggregate_id: int | None = None, trip_id: int | None = None, route_id: int | None = None,
                 date_from: date | None = None, date_to: date | None = None,
                 limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                 _: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    return await service.events(session, types=type, aggregate_type=aggregate_type, aggregate_id=aggregate_id,
                                trip_id=trip_id, route_id=route_id, date_from=date_from, date_to=date_to,
                                limit=limit, offset=offset)


@router.get("/trips/{trip_id}/timeline", response_model=list[EventOut])
async def timeline(trip_id: int, _: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    return await service.trip_timeline(session, trip_id)


@router.get("/trips", response_model=list[TripReport])
async def trips(date_from: date | None = None, date_to: date | None = None, route_id: int | None = None,
                _: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    return await service.trip_reports(session, date_from=date_from, date_to=date_to, route_id=route_id)


@router.get("/attendance", response_model=list[AttendanceRow])
async def attendance(date_from: date | None = None, date_to: date | None = None, route_id: int | None = None,
                     student_id: int | None = None, format: str = Query("json", pattern="^(json|csv)$"),
                     _: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    rows = await service.attendance(session, date_from=date_from, date_to=date_to,
                                    route_id=route_id, student_id=student_id)
    if format == "csv":
        return Response(service.attendance_csv(rows), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="attendance.csv"'})
    return rows
