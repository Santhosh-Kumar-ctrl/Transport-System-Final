from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import Principal, require_roles
from app.core.ratelimit import RateLimiter
from app.core.roles import Role
from app.modules.reports import service
from app.modules.reports.models import FoundItemStatus, ReportKind, ReportSeverity, ReportStatus
from app.modules.reports.schemas import (
    CloseIn,
    FoundItemIn,
    FoundItemOut,
    FoundItemUpdate,
    MessageIn,
    ReportAdminOut,
    ReportIn,
    ReportOut,
    TripOption,
)

router = APIRouter(tags=["reports"])
student = require_roles(Role.STUDENT)
admin_only = require_roles(Role.ADMIN)
staff = require_roles(Role.ADMIN, Role.DRIVER)

# Each report queues a model run and alerts every admin, so a student can't flood either.
report_limit = RateLimiter("reports", limit=settings.reports_per_hour, window_seconds=3600,
                           message="You've sent several reports in the last hour. Add to an earlier report "
                                   "or try again later.")


# ---------------- Student ----------------
@router.get("/reports/trip-options", response_model=list[TripOption])
async def trip_options(p: Principal = Depends(student), session: AsyncSession = Depends(get_session)):
    return await service.trip_options(session, p.id)


@router.post("/reports", response_model=ReportOut, status_code=201)
async def create_report(body: ReportIn, p: Principal = Depends(student), session: AsyncSession = Depends(get_session)):
    report_limit.hit(str(p.id))
    report = await service.create_report(session, p, body)
    await session.commit()
    return await service.student_view(session, report)


@router.get("/reports/mine", response_model=list[ReportOut])
async def my_reports(p: Principal = Depends(student), session: AsyncSession = Depends(get_session)):
    return await service.student_views(session, await service.list_mine(session, p.id))


@router.post("/reports/{report_id}/messages", response_model=ReportOut, status_code=201)
async def follow_up(report_id: int, body: MessageIn, p: Principal = Depends(student),
                    session: AsyncSession = Depends(get_session)):
    report = await service.add_student_message(session, report_id, p, body.body)
    await session.commit()
    return await service.student_view(session, report, with_messages=True)


# ---------------- Both ----------------
# No response_model: a union would let FastAPI coerce an admin view into the student shape (or the
# reverse). Each branch returns its own pydantic model, which is serialised as-is.
@router.get("/reports/{report_id}", response_model=None)
async def get_report(report_id: int, p: Principal = Depends(require_roles(Role.STUDENT, Role.ADMIN)),
                     session: AsyncSession = Depends(get_session)) -> ReportAdminOut | ReportOut:
    report = await service.get_report(session, report_id)
    if p.role == Role.ADMIN:
        return await service.admin_view(session, report, with_messages=True)
    service.ensure_can_read(report, p)
    return await service.student_view(session, report, with_messages=True)


# ---------------- Admin ----------------
@router.get("/reports", response_model=list[ReportAdminOut])
async def list_reports(status: ReportStatus | None = None, kind: ReportKind | None = None,
                       severity: ReportSeverity | None = None, _: Principal = Depends(admin_only),
                       session: AsyncSession = Depends(get_session)):
    reports = await service.list_reports(session, status=status, kind=kind, severity=severity)
    return await service.admin_views(session, reports)


@router.post("/reports/{report_id}/reply", response_model=ReportAdminOut)
async def reply(report_id: int, body: MessageIn, p: Principal = Depends(admin_only),
                session: AsyncSession = Depends(get_session)):
    report = await service.reply(session, report_id, p, body.body)
    await session.commit()
    return await service.admin_view(session, report, with_messages=True)


@router.post("/reports/{report_id}/close", response_model=ReportAdminOut)
async def close(report_id: int, body: CloseIn, p: Principal = Depends(admin_only),
                session: AsyncSession = Depends(get_session)):
    report = await service.close(session, report_id, p, body.note)
    await session.commit()
    return await service.admin_view(session, report, with_messages=True)


@router.post("/reports/{report_id}/reanalyse", response_model=ReportAdminOut)
async def reanalyse(report_id: int, p: Principal = Depends(admin_only), session: AsyncSession = Depends(get_session)):
    report = await service.request_analysis(session, report_id, p)
    await session.commit()
    return await service.admin_view(session, report, with_messages=True)


@router.post("/reports/{report_id}/match/{found_item_id}", response_model=ReportAdminOut)
async def match(report_id: int, found_item_id: int, p: Principal = Depends(admin_only),
                session: AsyncSession = Depends(get_session)):
    report = await service.match_found_item(session, report_id, found_item_id, p)
    await session.commit()
    return await service.admin_view(session, report, with_messages=True)


# ---------------- Found items ----------------
@router.post("/found-items", response_model=FoundItemOut, status_code=201)
async def log_found_item(body: FoundItemIn, p: Principal = Depends(staff), session: AsyncSession = Depends(get_session)):
    item = await service.log_found_item(session, p, body.trip_id, body.description)
    await session.commit()
    return await service.found_item_view(session, item)


@router.get("/found-items", response_model=list[FoundItemOut])
async def found_items(status: FoundItemStatus | None = None, p: Principal = Depends(staff),
                      session: AsyncSession = Depends(get_session)):
    return [await service.found_item_view(session, i) for i in await service.list_found_items(session, p, status)]


@router.patch("/found-items/{item_id}", response_model=FoundItemOut)
async def update_found_item(item_id: int, body: FoundItemUpdate, p: Principal = Depends(admin_only),
                            session: AsyncSession = Depends(get_session)):
    item = await service.set_found_item_status(session, item_id, body.status, p)
    await session.commit()
    return await service.found_item_view(session, item)
