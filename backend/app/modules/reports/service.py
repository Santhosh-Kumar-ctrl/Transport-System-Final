"""Student problem reports and lost-and-found.

Students file reports; the agent (agent.py) analyses them in the background; admins reply,
match lost items and close. Drivers and admins log found items.

Public API for other modules: recipient_id, get_report.

Privacy: event payloads never carry the student's id, and an anonymous report is published
with no actor, so neither the event log nor the history screens reveal who sent it.
"""

from datetime import timedelta

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.config import settings
from app.core.deps import Principal
from app.core.errors import Forbidden, InvalidState, NotFound
from app.core.roles import Role
from app.core.timeutil import now_utc, today_local
from app.modules.allocation import service as alloc_service
from app.modules.auth import service as auth_service
from app.modules.boarding.models import Boarding
from app.modules.master_data import service as md_service
from app.modules.master_data.models import Bus, Route, Stop
from app.modules.reports.models import (
    AnalysisStatus,
    FoundItem,
    FoundItemStatus,
    Report,
    ReportKind,
    ReportMessage,
    ReportSeverity,
    ReportStatus,
)
from app.modules.reports.schemas import (
    Analysis,
    FoundItemOut,
    MessageOut,
    ReportAdminOut,
    ReportIn,
    ReportOut,
    TripOption,
)
from app.modules.trips import service as trips_service
from app.modules.trips.models import Trip, TripStatus


# ---------------- Lookups ----------------
async def get_report(session: AsyncSession, report_id: int) -> Report:
    report = await session.get(Report, report_id)
    if report is None:
        raise NotFound(f"Report {report_id} not found")
    return report


async def recipient_id(session: AsyncSession, report_id: int) -> int:
    """Who to notify about staff activity on a report (for the notifications module)."""
    return (await get_report(session, report_id)).student_id


def ensure_can_read(report: Report, p: Principal) -> None:
    if p.role != Role.ADMIN and report.student_id != p.id:
        raise NotFound(f"Report {report.id} not found")  # don't reveal other students' reports exist


# ---------------- Trip picker ----------------
async def trip_options(session: AsyncSession, student_id: int) -> list[TripOption]:
    """The student's trips from the last few days that have departed (or should have):
    trips they boarded, plus every trip on their allocated route (covers "the bus never came")."""
    since_day = today_local() - timedelta(days=settings.report_lookback_days - 1)
    boarded_ids = set(await session.scalars(
        select(Boarding.trip_id).where(Boarding.student_id == student_id,
                                       Boarding.boarded_at >= now_utc() - timedelta(days=settings.report_lookback_days))
    ))
    trips: dict[int, Trip] = {}
    for tid in boarded_ids:
        trips[tid] = await trips_service.get_trip(session, tid)
    allocation = await alloc_service.get_active(session, student_id)
    if allocation:
        for offset in range(settings.report_lookback_days):
            for t in await trips_service.trips_for_route_on(session, allocation.route_id, since_day + timedelta(days=offset)):
                trips.setdefault(t.id, t)
    now = now_utc()
    out = []
    for t in sorted(trips.values(), key=lambda t: t.scheduled_departure, reverse=True):
        if t.service_date < since_day or (t.status == TripStatus.SCHEDULED and t.scheduled_departure > now):
            continue
        route = await md_service.get_route(session, t.route_id)
        out.append(TripOption(trip_id=t.id, service_date=t.service_date, direction=t.direction, status=t.status,
                              scheduled_departure=t.scheduled_departure, route_code=route.code,
                              route_color=route.color, boarded=t.id in boarded_ids))
    return out


# ---------------- Student actions ----------------
async def create_report(session: AsyncSession, p: Principal, data: ReportIn) -> Report:
    route_id = stop_id = None
    allocation = await alloc_service.get_active(session, p.id)
    if data.trip_id is not None:
        if data.trip_id not in {o.trip_id for o in await trip_options(session, p.id)}:
            raise InvalidState("Pick one of your trips from the last few days", code="trip_not_yours")
        trip = await trips_service.get_trip(session, data.trip_id)
        route_id = trip.route_id
    elif allocation:
        route_id = allocation.route_id
    if allocation and allocation.route_id == route_id:
        stop_id = allocation.stop_id

    report = Report(student_id=p.id, trip_id=data.trip_id, route_id=route_id, stop_id=stop_id, kind=data.kind,
                    description=data.description.strip(), anonymous=data.anonymous)
    session.add(report)
    await session.flush()
    payload = {"report_id": report.id, "kind": report.kind.value}
    if report.trip_id:
        payload["trip_id"] = report.trip_id
    await events.publish(session, "ReportSubmitted", payload, aggregate=("report", report.id),
                         actor_id=None if report.anonymous else p.id)
    return report


async def add_student_message(session: AsyncSession, report_id: int, p: Principal, body: str) -> Report:
    report = await get_report(session, report_id)
    ensure_can_read(report, p)
    if report.status == ReportStatus.CLOSED:
        raise InvalidState("This report is closed. Send a new report instead.", code="report_closed")
    session.add(ReportMessage(report_id=report.id, author_id=p.id, from_staff=False, body=body.strip()))
    report.status = ReportStatus.OPEN
    await session.flush()
    await events.publish(session, "ReportFollowUp", {"report_id": report.id, "kind": report.kind.value},
                         aggregate=("report", report.id), actor_id=None if report.anonymous else p.id)
    return report


async def list_mine(session: AsyncSession, student_id: int) -> list[Report]:
    return list(await session.scalars(
        select(Report).where(Report.student_id == student_id).order_by(Report.created_at.desc())
    ))


# ---------------- Admin actions ----------------
_SEVERITY_ORDER = case(
    (Report.severity == ReportSeverity.CRITICAL, 0), (Report.severity == ReportSeverity.HIGH, 1), else_=2
)


async def list_reports(session: AsyncSession, *, status: ReportStatus | None = None, kind: ReportKind | None = None,
                       severity: ReportSeverity | None = None, limit: int = 200) -> list[Report]:
    """Newest first, with open critical and high reports pinned to the top."""
    stmt = select(Report)
    if status:
        stmt = stmt.where(Report.status == status)
    if kind:
        stmt = stmt.where(Report.kind == kind)
    if severity:
        stmt = stmt.where(Report.severity == severity)
    pinned = case((Report.status == ReportStatus.CLOSED, 3), else_=_SEVERITY_ORDER)
    return list(await session.scalars(stmt.order_by(pinned, Report.created_at.desc()).limit(limit)))


async def reply(session: AsyncSession, report_id: int, p: Principal, body: str) -> Report:
    report = await get_report(session, report_id)
    if report.status == ReportStatus.CLOSED:
        raise InvalidState("This report is closed", code="report_closed")
    session.add(ReportMessage(report_id=report.id, author_id=p.id, from_staff=True, body=body.strip()))
    report.status = ReportStatus.REPLIED
    await session.flush()
    await events.publish(session, "ReportReplied",
                         {"report_id": report.id, "kind": report.kind.value, "body": body.strip()},
                         aggregate=("report", report.id), actor_id=p.id)
    return report


async def close(session: AsyncSession, report_id: int, p: Principal, note: str | None) -> Report:
    report = await get_report(session, report_id)
    if report.status == ReportStatus.CLOSED:
        raise InvalidState("This report is already closed", code="report_closed")
    report.status = ReportStatus.CLOSED
    report.closed_at = now_utc()
    report.closed_by = p.id
    report.resolution_note = note.strip() if note else None
    await session.flush()
    await events.publish(session, "ReportClosed",
                         {"report_id": report.id, "kind": report.kind.value, "note": report.resolution_note},
                         aggregate=("report", report.id), actor_id=p.id)
    return report


async def request_analysis(session: AsyncSession, report_id: int, p: Principal) -> Report:
    report = await get_report(session, report_id)
    report.analysis_status = AnalysisStatus.PENDING
    await session.flush()
    await events.publish(session, "ReportAnalysisRequested", {"report_id": report.id},
                         aggregate=("report", report.id), actor_id=p.id)
    return report


async def match_found_item(session: AsyncSession, report_id: int, item_id: int, p: Principal) -> Report:
    report = await get_report(session, report_id)
    if report.kind != ReportKind.LOST_ITEM:
        raise InvalidState("Only lost-item reports can be matched to a found item", code="not_lost_item")
    item = await get_found_item(session, item_id)
    if item.status != FoundItemStatus.UNCLAIMED:
        raise InvalidState(f"That item is already {item.status.value}", code="item_taken")
    if report.matched_found_item_id and report.matched_found_item_id != item.id:
        # Re-matched to another item: the first one goes back on the shelf.
        previous = await session.get(FoundItem, report.matched_found_item_id)
        if previous and previous.status == FoundItemStatus.MATCHED:
            previous.status = FoundItemStatus.UNCLAIMED
    item.status = FoundItemStatus.MATCHED
    report.matched_found_item_id = item.id
    await session.flush()
    await events.publish(session, "LostItemMatched",
                         {"report_id": report.id, "found_item_id": item.id, "description": item.description},
                         aggregate=("report", report.id), actor_id=p.id)
    return report


async def pending_analysis(session: AsyncSession, older_than_seconds: int = 30) -> list[int]:
    """Reports whose analysis never finished (e.g. the server restarted mid-way)."""
    cutoff = now_utc() - timedelta(seconds=older_than_seconds)
    return list(await session.scalars(
        select(Report.id).where(Report.analysis_status == AnalysisStatus.PENDING, Report.updated_at < cutoff)
    ))


# ---------------- Views ----------------
async def _messages(session: AsyncSession, report_id: int) -> list[MessageOut]:
    rows = await session.scalars(
        select(ReportMessage).where(ReportMessage.report_id == report_id).order_by(ReportMessage.id)
    )
    return [MessageOut(id=m.id, from_staff=m.from_staff, body=m.body, created_at=m.created_at) for m in rows]


class _Lookups:
    """Trips, routes, stops, buses and students for a list of reports, fetched once each."""

    def __init__(self) -> None:
        self.trips: dict[int, Trip] = {}
        self.routes: dict[int, Route] = {}
        self.stops: dict[int, Stop] = {}
        self.buses: dict[int, Bus] = {}
        self.students: dict = {}

    @classmethod
    async def load(cls, session: AsyncSession, reports: list[Report], *, people: bool) -> "_Lookups":
        lk = cls()

        async def by_id(model, ids):
            ids = {i for i in ids if i}
            return {o.id: o for o in await session.scalars(select(model).where(model.id.in_(ids)))} if ids else {}

        lk.trips = await by_id(Trip, (r.trip_id for r in reports))
        lk.routes = await by_id(Route, (r.route_id for r in reports))
        lk.stops = await by_id(Stop, (r.stop_id for r in reports if not r.anonymous))
        if people:
            lk.buses = await by_id(Bus, (t.bus_id for t in lk.trips.values()))
            lk.students = await auth_service.briefs(session, [r.student_id for r in reports if not r.anonymous])
        return lk


async def _base(session: AsyncSession, r: Report, lk: _Lookups, with_messages: bool) -> dict:
    trip, route = lk.trips.get(r.trip_id), lk.routes.get(r.route_id)
    # An anonymous report never shows the reporter's allocated stop: it narrows down who sent it.
    stop = None if r.anonymous else lk.stops.get(r.stop_id)
    return {
        "id": r.id, "kind": r.kind, "description": r.description, "status": r.status, "anonymous": r.anonymous,
        "trip_id": r.trip_id, "service_date": trip.service_date if trip else None,
        "direction": trip.direction if trip else None,
        "route_code": route.code if route else None, "route_color": route.color if route else None,
        "stop_name": stop.name if stop else None, "created_at": r.created_at, "updated_at": r.updated_at,
        "resolution_note": r.resolution_note,
        "messages": await _messages(session, r.id) if with_messages else [],
    }


async def student_views(
    session: AsyncSession, reports: list[Report], *, with_messages: bool = False
) -> list[ReportOut]:
    lk = await _Lookups.load(session, reports, people=False)
    return [ReportOut(**await _base(session, r, lk, with_messages)) for r in reports]


async def student_view(session: AsyncSession, r: Report, *, with_messages: bool = False) -> ReportOut:
    return (await student_views(session, [r], with_messages=with_messages))[0]


async def admin_views(
    session: AsyncSession, reports: list[Report], *, with_messages: bool = False
) -> list[ReportAdminOut]:
    lk = await _Lookups.load(session, reports, people=True)
    out = []
    for r in reports:
        trip = lk.trips.get(r.trip_id)
        bus = lk.buses.get(trip.bus_id) if trip else None
        who = None if r.anonymous else lk.students.get(r.student_id)
        out.append(ReportAdminOut(
            **await _base(session, r, lk, with_messages),
            student_id=who.id if who else None, student_name=who.full_name if who else None,
            roll_no=who.roll_no if who else None,
            bus_registration_no=bus.registration_no if bus else None,
            severity=r.severity, analysis_status=r.analysis_status,
            analysis=Analysis.model_validate(r.analysis) if r.analysis else None,
            analysed_by=r.analysed_by, analysed_at=r.analysed_at,
            matched_found_item_id=r.matched_found_item_id, closed_at=r.closed_at,
        ))
    return out


async def admin_view(session: AsyncSession, r: Report, *, with_messages: bool = False) -> ReportAdminOut:
    return (await admin_views(session, [r], with_messages=with_messages))[0]


# ---------------- Found items ----------------
async def get_found_item(session: AsyncSession, item_id: int) -> FoundItem:
    item = await session.get(FoundItem, item_id)
    if item is None:
        raise NotFound(f"Found item {item_id} not found")
    return item


async def log_found_item(session: AsyncSession, p: Principal, trip_id: int | None, description: str) -> FoundItem:
    bus_id = None
    if trip_id is not None:
        trip = await trips_service.get_trip(session, trip_id)
        if p.role == Role.DRIVER:
            if trip.driver_id != p.id:
                raise Forbidden("You can only log items found on your own trips")
            if trip.service_date != today_local() or trip.status not in (TripStatus.IN_PROGRESS, TripStatus.COMPLETED):
                raise InvalidState("Log found items on a trip that is running or ended today", code="bad_trip_state")
        bus_id = trip.bus_id
    elif p.role == Role.DRIVER:
        raise InvalidState("Pick the trip the item was found on", code="trip_required")
    item = FoundItem(trip_id=trip_id, bus_id=bus_id, logged_by=p.id, description=description.strip())
    session.add(item)
    await session.flush()
    await events.publish(session, "FoundItemLogged", {"found_item_id": item.id, "description": item.description},
                         aggregate=("found_item", item.id), actor_id=p.id)
    return item


async def list_found_items(session: AsyncSession, p: Principal, status: FoundItemStatus | None) -> list[FoundItem]:
    stmt = select(FoundItem).order_by(FoundItem.created_at.desc()).limit(200)
    if status:
        stmt = stmt.where(FoundItem.status == status)
    if p.role == Role.DRIVER:
        stmt = stmt.where(FoundItem.logged_by == p.id)
    return list(await session.scalars(stmt))


async def set_found_item_status(session: AsyncSession, item_id: int, status: FoundItemStatus, p: Principal) -> FoundItem:
    item = await get_found_item(session, item_id)
    item.status = status
    await session.flush()
    return item


async def found_item_view(session: AsyncSession, item: FoundItem) -> FoundItemOut:
    trip = await trips_service.get_trip(session, item.trip_id) if item.trip_id else None
    route = await md_service.get_route(session, trip.route_id) if trip else None
    bus = await md_service.get_bus(session, item.bus_id) if item.bus_id else None
    who = (await auth_service.briefs(session, [item.logged_by])).get(item.logged_by) if item.logged_by else None
    return FoundItemOut(id=item.id, description=item.description, status=item.status, trip_id=item.trip_id,
                        route_code=route.code if route else None,
                        bus_registration_no=bus.registration_no if bus else None,
                        logged_by_name=who.full_name if who else None, created_at=item.created_at)
