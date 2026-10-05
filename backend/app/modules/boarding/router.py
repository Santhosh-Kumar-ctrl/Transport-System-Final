from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import Principal, require_roles
from app.core.roles import Role
from app.modules.boarding import service
from app.modules.boarding.schemas import (
    AttendanceOut,
    BoardingReceipt,
    CheckInIn,
    ManualBoardIn,
    Roster,
    TripQR,
)

router = APIRouter(prefix="/boarding", tags=["boarding"])
operator = require_roles(Role.ADMIN, Role.DRIVER)


@router.get("/trips/{trip_id}/qr", response_model=TripQR)
async def trip_qr(trip_id: int, p: Principal = Depends(operator), session: AsyncSession = Depends(get_session)):
    """Driver app polls this every `ttl_seconds` and renders `token` as a QR code."""
    return await service.issue_trip_qr(session, trip_id, p)


@router.post("/check-in", response_model=BoardingReceipt, status_code=201)
async def check_in(body: CheckInIn, p: Principal = Depends(require_roles(Role.STUDENT)),
                   session: AsyncSession = Depends(get_session)):
    receipt = await service.check_in(session, body.token, p)
    await session.commit()
    return receipt


@router.post("/trips/{trip_id}/manual", response_model=BoardingReceipt, status_code=201)
async def manual_board(trip_id: int, body: ManualBoardIn, p: Principal = Depends(operator),
                       session: AsyncSession = Depends(get_session)):
    receipt = await service.manual_board(session, trip_id, p, student_id=body.student_id, roll_no=body.roll_no)
    await session.commit()
    return receipt


@router.get("/trips/{trip_id}/roster", response_model=Roster)
async def roster(trip_id: int, p: Principal = Depends(operator), session: AsyncSession = Depends(get_session)):
    return await service.roster(session, trip_id, p)


@router.get("/me/attendance", response_model=list[AttendanceOut])
async def my_attendance(limit: int = Query(60, ge=1, le=365), p: Principal = Depends(require_roles(Role.STUDENT)),
                        session: AsyncSession = Depends(get_session)):
    return await service.student_attendance(session, p.id, limit)
