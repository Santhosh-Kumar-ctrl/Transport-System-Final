"""QR boarding and attendance.

Flow: the driver's app shows a trip QR that rotates every QR_TTL_SECONDS; the
student scans it and POSTs the token. A photo of the QR forwarded to someone not on
the bus stops working within seconds.

Public API for other modules: boarded_count(s), boarded_student_ids, finalize_attendance,
reconcile_attendance.
"""

import secrets
from datetime import date, timedelta

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.config import settings
from app.core.deps import Principal
from app.core.errors import Conflict, Forbidden, InvalidState, Unauthorized
from app.core.events import DomainEvent
from app.core.roles import Role
from app.core.security import sign, verify
from app.core.timeutil import now_utc, today_local
from app.modules.allocation import service as alloc_service
from app.modules.auth import service as auth_service
from app.modules.auth.models import User
from app.modules.boarding.models import AttendanceRecord, AttendanceStatus, Boarding, BoardingMethod
from app.modules.boarding.schemas import AttendanceOut, BoardingReceipt, Roster, RosterEntry, TripQR
from app.modules.master_data import service as md_service
from app.modules.master_data.models import Route, Stop
from app.modules.trips import service as trips_service
from app.modules.trips.models import Trip, TripStatus

QR_TOKEN_TYPE = "board"


def _ensure_operator(trip: Trip, p: Principal) -> None:
    if p.role == Role.ADMIN:
        return
    if p.role != Role.DRIVER or trip.driver_id != p.id:
        raise Forbidden("Only the assigned driver can do this")


async def boarded_count(session: AsyncSession, trip_id: int) -> int:
    return await session.scalar(select(func.count()).select_from(Boarding).where(Boarding.trip_id == trip_id))


async def boarded_counts(session: AsyncSession, trip_ids: list[int]) -> dict[int, int]:
    """boarded_count for several trips in one query (trips nobody boarded are left out)."""
    if not trip_ids:
        return {}
    rows = await session.execute(
        select(Boarding.trip_id, func.count()).where(Boarding.trip_id.in_(trip_ids)).group_by(Boarding.trip_id)
    )
    return {trip_id: n for trip_id, n in rows.all()}


async def boarded_student_ids(session: AsyncSession, trip_id: int) -> set[int]:
    return set(await session.scalars(select(Boarding.student_id).where(Boarding.trip_id == trip_id)))


# ---------------- QR ----------------
async def issue_trip_qr(session: AsyncSession, trip_id: int, p: Principal) -> TripQR:
    trip = await trips_service.get_trip(session, trip_id)
    _ensure_operator(trip, p)
    if trip.status != TripStatus.IN_PROGRESS:
        raise InvalidState("Start the trip before showing the boarding QR", code="bad_trip_state")
    route = await md_service.get_route(session, trip.route_id)
    bus = await md_service.get_bus(session, trip.bus_id)
    ttl = settings.qr_ttl_seconds
    issued = now_utc()
    token = sign({"typ": QR_TOKEN_TYPE, "trip": trip.id, "n": secrets.token_urlsafe(6)}, timedelta(seconds=ttl))
    return TripQR(
        token=token, trip_id=trip.id, issued_at=issued, expires_at=issued + timedelta(seconds=ttl),
        ttl_seconds=ttl, route_code=route.code, route_color=route.color,
        bus_registration_no=bus.registration_no,
    )


def _decode_qr(token: str) -> int:
    # Map token failures to 422s: a 401 would make the app think its *login* expired.
    try:
        claims = verify(token, QR_TOKEN_TYPE)
    except Unauthorized as exc:
        if exc.code == "token_expired":
            raise InvalidState("This QR code has expired. Scan the code on the driver's screen again.",
                               code="qr_expired") from exc
        raise InvalidState("Not a valid boarding QR code", code="qr_invalid") from exc
    return int(claims["trip"])


# ---------------- Boarding ----------------
async def _board(
    session: AsyncSession, trip: Trip, student: User, method: BoardingMethod, *, recorded_by: int | None
) -> BoardingReceipt:
    if trip.status != TripStatus.IN_PROGRESS:
        raise InvalidState("This trip is not running right now", code="bad_trip_state")
    if student.role != Role.STUDENT or not student.is_active:
        raise Forbidden("Only active students can board")

    allocation = await alloc_service.get_active(session, student.id)
    match = bool(allocation and allocation.route_id == trip.route_id)
    boarding = Boarding(
        trip_id=trip.id, student_id=student.id, stop_id=allocation.stop_id if match else None,
        method=method, allocation_match=match, boarded_at=now_utc(), recorded_by=recorded_by,
    )
    try:
        async with session.begin_nested():
            session.add(boarding)
            await session.flush()
    except IntegrityError as exc:
        raise Conflict("Already boarded on this trip", code="already_boarded") from exc

    count = await boarded_count(session, trip.id)
    await events.publish(
        session, "StudentBoarded",
        {"trip_id": trip.id, "route_id": trip.route_id, "bus_id": trip.bus_id,
         "driver_id": trip.driver_id, "student_id": student.id, "student_name": student.full_name,
         "stop_id": boarding.stop_id, "allocation_match": match, "method": method.value,
         "boarded_count": count},
        aggregate=("trip", trip.id), actor_id=recorded_by or student.id,
    )
    if not match:
        await events.publish(
            session, "UnallocatedBoarding",
            {"trip_id": trip.id, "route_id": trip.route_id, "driver_id": trip.driver_id,
             "student_id": student.id, "student_name": student.full_name,
             "allocated_route_id": allocation.route_id if allocation else None},
            aggregate=("trip", trip.id), actor_id=recorded_by or student.id,
        )

    route = await md_service.get_route(session, trip.route_id)
    bus = await md_service.get_bus(session, trip.bus_id)
    stop = await session.get(Stop, boarding.stop_id) if boarding.stop_id else None
    if match:
        message = f"Boarded route {route.code}. Have a good ride."
    elif allocation:
        message = "Boarded, but this bus is not your allocated route. The driver and transport office have been told."
    else:
        message = "Boarded, but you have no route allocation yet. The transport office has been told."
    return BoardingReceipt(
        boarding_id=boarding.id, trip_id=trip.id, student_id=student.id, student_name=student.full_name,
        boarded_at=boarding.boarded_at, method=method, allocation_match=match,
        route_code=route.code, route_name=route.name, route_color=route.color,
        bus_registration_no=bus.registration_no, stop_name=stop.name if stop else None,
        boarded_count=count, message=message,
    )


async def check_in(session: AsyncSession, token: str, p: Principal) -> BoardingReceipt:
    if p.role != Role.STUDENT:
        raise Forbidden("Only students check in by scanning")
    trip_id = _decode_qr(token)
    trip = await trips_service.get_trip(session, trip_id)
    student = await auth_service.get_user(session, p.id)
    return await _board(session, trip, student, BoardingMethod.QR, recorded_by=None)


async def manual_board(
    session: AsyncSession, trip_id: int, p: Principal, *, student_id: int | None, roll_no: str | None
) -> BoardingReceipt:
    trip = await trips_service.get_trip(session, trip_id)
    _ensure_operator(trip, p)
    if student_id is not None:
        student = await auth_service.get_user(session, student_id)
    else:
        student = await auth_service.find_student_by_roll_no(session, roll_no or "")
    return await _board(session, trip, student, BoardingMethod.MANUAL, recorded_by=p.id)


async def roster(session: AsyncSession, trip_id: int, p: Principal) -> Roster:
    trip = await trips_service.get_trip(session, trip_id)
    if p.role != Role.ADMIN:
        _ensure_operator(trip, p)
    bus = await md_service.get_bus(session, trip.bus_id)
    allocations = await alloc_service.active_on_route(session, trip.route_id)
    boardings = {b.student_id: b for b in await session.scalars(select(Boarding).where(Boarding.trip_id == trip.id))}
    alloc_by_student = {a.student_id: a for a in allocations}
    student_ids = set(alloc_by_student) | set(boardings)
    briefs = await auth_service.briefs(session, list(student_ids))
    stop_ids = {a.stop_id for a in allocations}
    stops = {s.id: s for s in await session.scalars(select(Stop).where(Stop.id.in_(stop_ids)))} if stop_ids else {}
    visit_order = {e.stop_id: e.sequence for e in trip.stop_events}

    entries = []
    for sid in student_ids:
        a, b, st = alloc_by_student.get(sid), boardings.get(sid), briefs[sid]
        stop = stops.get(a.stop_id) if a else None
        entries.append(RosterEntry(
            student_id=sid, full_name=st.full_name, roll_no=st.roll_no,
            stop_id=stop.id if stop else None, stop_name=stop.name if stop else None,
            allocated=a is not None, boarded=b is not None,
            boarded_at=b.boarded_at if b else None, method=b.method if b else None,
        ))
    entries.sort(key=lambda e: (visit_order.get(e.stop_id, 999), e.full_name))
    return Roster(trip_id=trip.id, capacity=bus.capacity, allocated_count=len(allocations),
                  boarded_count=len(boardings), entries=entries)


# ---------------- Attendance ----------------
async def finalize_attendance(session: AsyncSession, trip_id: int) -> dict | None:
    """Write attendance for a finished trip. Idempotent: returns None if already done."""
    trip = await trips_service.get_trip(session, trip_id)
    written = await session.scalar(select(AttendanceRecord.id).where(AttendanceRecord.trip_id == trip.id).limit(1))
    if written or await session.scalar(select(_finalized(trip.id))):
        return None
    boardings = {b.student_id: b for b in await session.scalars(select(Boarding).where(Boarding.trip_id == trip.id))}
    allocated = [a for a in await alloc_service.active_on_route(session, trip.route_id)
                 if a.valid_from <= trip.service_date]
    counts = {s: 0 for s in AttendanceStatus}
    seen: set[int] = set()
    for a in allocated:
        b = boardings.get(a.student_id)
        status = AttendanceStatus.PRESENT if b else AttendanceStatus.ABSENT
        counts[status] += 1
        seen.add(a.student_id)
        session.add(AttendanceRecord(trip_id=trip.id, student_id=a.student_id, route_id=trip.route_id,
                                     service_date=trip.service_date, status=status,
                                     boarding_id=b.id if b else None))
    for sid, b in boardings.items():
        if sid in seen:
            continue
        counts[AttendanceStatus.PRESENT_UNALLOCATED] += 1
        session.add(AttendanceRecord(trip_id=trip.id, student_id=sid, route_id=trip.route_id,
                                     service_date=trip.service_date,
                                     status=AttendanceStatus.PRESENT_UNALLOCATED, boarding_id=b.id))
    await session.flush()
    summary = {"trip_id": trip.id, "route_id": trip.route_id, "service_date": trip.service_date,
               "present": counts[AttendanceStatus.PRESENT], "absent": counts[AttendanceStatus.ABSENT],
               "present_unallocated": counts[AttendanceStatus.PRESENT_UNALLOCATED]}
    await events.publish(session, "AttendanceFinalized", summary, aggregate=("trip", trip.id))
    return summary


def _finalized(trip_id):
    """The AttendanceFinalized event exists for the trip (also covers trips with no riders)."""
    return exists().where(DomainEvent.type == "AttendanceFinalized", DomainEvent.aggregate_type == "trip",
                          DomainEvent.aggregate_id == trip_id)


async def reconcile_attendance(session: AsyncSession, *, days: int = 3) -> list[int]:
    """Finalise attendance for recent finished trips whose TripEnded/TripCancelled handler never ran
    (the server restarted, or the handler failed). Returns the trip ids it finalised."""
    since: date = today_local() - timedelta(days=days)
    settled = now_utc() - timedelta(minutes=2)  # leave just-ended trips to their own handler
    trip_ids = list(await session.scalars(
        select(Trip.id).where(
            Trip.service_date >= since, Trip.ended_at < settled,
            or_(Trip.status == TripStatus.COMPLETED,
                (Trip.status == TripStatus.CANCELLED) & Trip.started_at.is_not(None)),
            ~exists().where(AttendanceRecord.trip_id == Trip.id),
            ~exists().where(DomainEvent.type == "AttendanceFinalized", DomainEvent.aggregate_type == "trip",
                            DomainEvent.aggregate_id == Trip.id),
        )
    ))
    done = []
    for trip_id in trip_ids:
        if await finalize_attendance(session, trip_id) is not None:
            done.append(trip_id)
    return done


async def student_attendance(session: AsyncSession, student_id: int, limit: int = 60) -> list[AttendanceOut]:
    rows = await session.execute(
        select(AttendanceRecord, Trip, Route, Boarding.boarded_at)
        .join(Trip, Trip.id == AttendanceRecord.trip_id)
        .join(Route, Route.id == AttendanceRecord.route_id)
        .outerjoin(Boarding, Boarding.id == AttendanceRecord.boarding_id)
        .where(AttendanceRecord.student_id == student_id)
        .order_by(Trip.scheduled_departure.desc())
        .limit(limit)
    )
    return [
        AttendanceOut(trip_id=t.id, service_date=ar.service_date, route_id=r.id, route_code=r.code,
                      route_color=r.color, direction=t.direction, status=ar.status, boarded_at=boarded_at)
        for ar, t, r, boarded_at in rows.all()
    ]


async def student_boardings_on(session: AsyncSession, student_id: int, trip_ids: list[int]) -> dict[int, Boarding]:
    if not trip_ids:
        return {}
    rows = await session.scalars(
        select(Boarding).where(Boarding.student_id == student_id, Boarding.trip_id.in_(trip_ids))
    )
    return {b.trip_id: b for b in rows}
