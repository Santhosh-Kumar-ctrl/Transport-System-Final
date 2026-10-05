"""Operational history: read-only views over the domain event log, trips and attendance."""

import csv
import io
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import DomainEvent, payload_text
from app.core.timeutil import local_tz
from app.modules.auth import service as auth_service
from app.modules.auth.models import StudentProfile, User
from app.modules.boarding.models import AttendanceRecord, AttendanceStatus, Boarding
from app.modules.delay_monitor.models import DelayReport, DelaySource
from app.modules.history.schemas import AttendanceRow, EventOut, TripReport
from app.modules.master_data.models import Bus, Route
from app.modules.trips.models import Trip, TripStopEvent


def _day_bounds(d_from: date | None, d_to: date | None) -> tuple[datetime | None, datetime | None]:
    tz = local_tz()
    start = datetime.combine(d_from, time.min, tzinfo=tz) if d_from else None
    end = datetime.combine(d_to + timedelta(days=1), time.min, tzinfo=tz) if d_to else None
    return start, end


async def _with_actor_names(session: AsyncSession, rows: list[DomainEvent]) -> list[EventOut]:
    names = {uid: u.full_name for uid, u in
             (await auth_service.get_users(session, [r.actor_id for r in rows if r.actor_id])).items()}
    return [EventOut.model_validate(r).model_copy(update={"actor_name": names.get(r.actor_id)}) for r in rows]


async def events(
    session: AsyncSession,
    *,
    types: list[str] | None = None,
    aggregate_type: str | None = None,
    aggregate_id: int | None = None,
    trip_id: int | None = None,
    route_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[EventOut]:
    stmt = select(DomainEvent)
    if types:
        stmt = stmt.where(DomainEvent.type.in_(types))
    if aggregate_type:
        stmt = stmt.where(DomainEvent.aggregate_type == aggregate_type)
    if aggregate_id is not None:
        stmt = stmt.where(DomainEvent.aggregate_id == aggregate_id)
    if trip_id is not None:
        stmt = stmt.where(or_(
            and_(DomainEvent.aggregate_type == "trip", DomainEvent.aggregate_id == trip_id),
            payload_text("trip_id") == str(trip_id),
        ))
    if route_id is not None:
        stmt = stmt.where(payload_text("route_id") == str(route_id))
    start, end = _day_bounds(date_from, date_to)
    if start:
        stmt = stmt.where(DomainEvent.occurred_at >= start)
    if end:
        stmt = stmt.where(DomainEvent.occurred_at < end)
    stmt = stmt.order_by(DomainEvent.occurred_at.desc(), DomainEvent.id.desc()).limit(limit).offset(offset)
    return await _with_actor_names(session, list(await session.scalars(stmt)))


async def trip_timeline(session: AsyncSession, trip_id: int) -> list[EventOut]:
    rows = await events(session, trip_id=trip_id, limit=1000)
    return list(reversed(rows))  # oldest first reads like a story


async def trip_reports(
    session: AsyncSession, *, date_from: date | None = None, date_to: date | None = None,
    route_id: int | None = None, limit: int = 200,
) -> list[TripReport]:
    boarded = (select(Boarding.trip_id, func.count().label("n")).group_by(Boarding.trip_id).subquery())
    att = (select(
        AttendanceRecord.trip_id,
        func.count().filter(AttendanceRecord.status != AttendanceStatus.ABSENT).label("present"),
        func.count().filter(AttendanceRecord.status == AttendanceStatus.ABSENT).label("absent"),
    ).group_by(AttendanceRecord.trip_id).subquery())
    delays = (select(
        DelayReport.trip_id, func.count().filter(DelayReport.source != DelaySource.RECOVERED).label("alerts"),
    ).group_by(DelayReport.trip_id).subquery())
    max_delay = (select(TripStopEvent.trip_id, func.max(TripStopEvent.delay_min).label("d"))
                 .group_by(TripStopEvent.trip_id).subquery())

    stmt = (
        select(Trip, Route, Bus, User.full_name,
               func.coalesce(boarded.c.n, 0), func.coalesce(att.c.present, 0), func.coalesce(att.c.absent, 0),
               func.coalesce(delays.c.alerts, 0), func.coalesce(max_delay.c.d, 0))
        .join(Route, Route.id == Trip.route_id)
        .join(Bus, Bus.id == Trip.bus_id)
        .join(User, User.id == Trip.driver_id)
        .outerjoin(boarded, boarded.c.trip_id == Trip.id)
        .outerjoin(att, att.c.trip_id == Trip.id)
        .outerjoin(delays, delays.c.trip_id == Trip.id)
        .outerjoin(max_delay, max_delay.c.trip_id == Trip.id)
    )
    if date_from:
        stmt = stmt.where(Trip.service_date >= date_from)
    if date_to:
        stmt = stmt.where(Trip.service_date <= date_to)
    if route_id:
        stmt = stmt.where(Trip.route_id == route_id)
    stmt = stmt.order_by(Trip.scheduled_departure.desc()).limit(limit)
    out = []
    for t, r, b, driver_name, n_boarded, present, absent, alerts, max_d in (await session.execute(stmt)).all():
        out.append(TripReport(
            trip_id=t.id, service_date=t.service_date, route_id=r.id, route_code=r.code, route_color=r.color,
            direction=t.direction, bus_registration_no=b.registration_no, driver_name=driver_name,
            status=t.status, scheduled_departure=t.scheduled_departure, started_at=t.started_at,
            ended_at=t.ended_at, max_delay_min=max(0, max_d), delay_alerts=alerts, boarded=n_boarded,
            present=present, absent=absent,
        ))
    return out


async def attendance(
    session: AsyncSession, *, date_from: date | None = None, date_to: date | None = None,
    route_id: int | None = None, student_id: int | None = None, limit: int = 2000,
) -> list[AttendanceRow]:
    stmt = (
        select(AttendanceRecord, Trip.direction, Route.code, User.full_name, StudentProfile.roll_no,
               Boarding.boarded_at)
        .join(Trip, Trip.id == AttendanceRecord.trip_id)
        .join(Route, Route.id == AttendanceRecord.route_id)
        .join(User, User.id == AttendanceRecord.student_id)
        .outerjoin(StudentProfile, StudentProfile.user_id == User.id)
        .outerjoin(Boarding, Boarding.id == AttendanceRecord.boarding_id)
    )
    if date_from:
        stmt = stmt.where(AttendanceRecord.service_date >= date_from)
    if date_to:
        stmt = stmt.where(AttendanceRecord.service_date <= date_to)
    if route_id:
        stmt = stmt.where(AttendanceRecord.route_id == route_id)
    if student_id:
        stmt = stmt.where(AttendanceRecord.student_id == student_id)
    stmt = stmt.order_by(
        AttendanceRecord.service_date.desc(), Route.code,
        case((AttendanceRecord.status == AttendanceStatus.ABSENT, 0), else_=1), User.full_name,
    ).limit(limit)
    return [
        AttendanceRow(service_date=ar.service_date, trip_id=ar.trip_id, route_code=code, direction=direction,
                      student_id=ar.student_id, student_name=name, roll_no=roll, status=ar.status,
                      boarded_at=boarded_at)
        for ar, direction, code, name, roll, boarded_at in (await session.execute(stmt)).all()
    ]


def attendance_csv(rows: list[AttendanceRow]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["date", "trip_id", "route", "direction", "roll_no", "student", "status", "boarded_at"])
    for r in rows:
        w.writerow([r.service_date, r.trip_id, r.route_code, r.direction.value, r.roll_no or "",
                    r.student_name, r.status.value,
                    r.boarded_at.astimezone(local_tz()).strftime("%H:%M") if r.boarded_at else ""])
    return buf.getvalue()
