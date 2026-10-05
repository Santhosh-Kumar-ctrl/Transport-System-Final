"""In-app notifications: inbox storage + deciding *who* hears about *what*.

`messages_for(event)` is pure-ish recipient/wording logic (reads DB, writes nothing),
so it is unit-testable and reusable by Team B's agent. `deliver()` persists + pushes.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import Event, jsonable
from app.core.realtime import hub
from app.core.timeutil import local_tz, now_utc
from app.modules.allocation import service as alloc_service
from app.modules.auth import service as auth_service
from app.modules.boarding import service as boarding_service
from app.modules.master_data import service as md_service
from app.modules.master_data.models import Stop
from app.modules.notifications.models import Notification, Severity
from app.modules.notifications.schemas import Message, NotificationOut
from app.modules.reports import service as reports_service
from app.modules.trips import service as trips_service
from app.modules.trips.models import Direction, TripStatus


def _hm(value: str | datetime) -> str:
    dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    return dt.astimezone(local_tz()).strftime("%H:%M")


# ---------------- Inbox ----------------
async def create(session: AsyncSession, msg: Message) -> list[Notification]:
    payload = jsonable(msg.payload)
    rows = [
        Notification(user_id=uid, type=msg.type, title=msg.title, body=msg.body,
                     severity=msg.severity, payload=payload)
        for uid in dict.fromkeys(msg.user_ids)  # de-duplicate, keep order
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def push(rows: list[Notification]) -> None:
    for n in rows:
        await hub.send_to_user(n.user_id, "notification", NotificationOut.model_validate(n).model_dump())


async def deliver(session: AsyncSession, messages: list[Message]) -> list[Notification]:
    """Persist all messages, commit, then push over WebSocket."""
    rows: list[Notification] = []
    for m in messages:
        if m.user_ids:
            rows += await create(session, m)
    await session.commit()
    await push(rows)
    return rows


async def list_for_user(
    session: AsyncSession, user_id: int, *, unread_only: bool = False, limit: int = 50, before_id: int | None = None
) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if before_id:
        stmt = stmt.where(Notification.id < before_id)
    return list(await session.scalars(stmt.order_by(Notification.id.desc()).limit(limit)))


async def unread_count(session: AsyncSession, user_id: int) -> int:
    return await session.scalar(
        select(func.count()).select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    )


async def mark_read(session: AsyncSession, user_id: int, notification_id: int | None = None) -> int:
    stmt = update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    if notification_id is not None:
        stmt = stmt.where(Notification.id == notification_id)
    result = await session.execute(stmt.values(read_at=now_utc()))
    return result.rowcount or 0


async def delete_old_read(session: AsyncSession, older_than_days: int) -> int:
    """Housekeeping: read notifications older than this are deleted. Unread ones are kept."""
    cutoff = now_utc() - timedelta(days=older_than_days)
    result = await session.execute(
        delete(Notification).where(Notification.read_at.is_not(None), Notification.created_at < cutoff)
    )
    return result.rowcount or 0


# ---------------- Recipient resolution ----------------
async def _student_stops(session: AsyncSession, route_id: int) -> dict[int, int]:
    """student_id -> allocated stop_id, for active allocations on the route."""
    return {a.student_id: a.stop_id for a in await alloc_service.active_on_route(session, route_id)}


async def delay_student_targets(session: AsyncSession, payload: dict) -> dict[int, dict]:
    """Students affected by a delay, each with the affected stop entry relevant to them.

    Pickup: students waiting at a stop the bus hasn't reached (and who haven't boarded).
    Drop:   students on board (trip running) or all allocated riders (not started yet).
    """
    trip = await trips_service.get_trip(session, payload["trip_id"])
    by_stop = {s["stop_id"]: s for s in payload.get("affected_stops") or payload.get("remaining_stops") or []}
    stops_of = await _student_stops(session, trip.route_id)
    boarded = await boarding_service.boarded_student_ids(session, trip.id)
    if trip.direction == Direction.PICKUP:
        return {sid: by_stop[stop] for sid, stop in stops_of.items() if stop in by_stop and sid not in boarded}
    riders = boarded if trip.status == TripStatus.IN_PROGRESS else set(stops_of)
    return {sid: by_stop.get(stops_of.get(sid, -1), {}) for sid in riders}


async def approach_student_targets(session: AsyncSession, payload: dict) -> list[int]:
    """Riders of the stop the bus is nearing.

    Pickup: allocated to that stop and not boarded yet. Drop: on board and getting off there.
    """
    trip = await trips_service.get_trip(session, payload["trip_id"])
    stops_of = await _student_stops(session, trip.route_id)
    boarded = await boarding_service.boarded_student_ids(session, trip.id)
    at_stop = [sid for sid, stop in stops_of.items() if stop == payload["stop_id"]]
    if trip.direction == Direction.PICKUP:
        return [sid for sid in at_stop if sid not in boarded]
    return [sid for sid in at_stop if sid in boarded]


async def messages_for(session: AsyncSession, ev: Event) -> list[Message]:
    p = ev.payload
    t = ev.type
    admins = await auth_service.admin_ids(session)
    route = await md_service.get_route(session, p["route_id"]) if "route_id" in p else None
    code = route.code if route else "?"
    base = {"trip_id": p.get("trip_id"), "route_id": p.get("route_id"), "route_code": code,
            "route_color": route.color if route else None}

    if t == "TripDelayed":
        n = p["delay_min"]
        targets = await delay_student_targets(session, p)
        # The last alert step goes to the transport office only: riders already know it's late.
        to_riders = p.get("notify_students", True)
        msgs = []
        for sid, stop in (targets.items() if to_riders else ()):
            where = (f" Expected at {stop['stop_name']} around {_hm(stop['expected_at'])}"
                     f" (scheduled {_hm(stop['scheduled_at'])}).") if stop else ""
            reason = f" Reason: {p['reason']}." if p.get("reason") else ""
            msgs.append(Message(user_ids=[sid], type=t, severity=Severity.WARNING,
                                title=f"Route {code} is running {n} min late",
                                body=f"Your bus is delayed.{where}{reason}",
                                payload={**base, "delay_min": n, "stop": stop or None}))
        source_text = {
            "start": "late departure", "stop_arrival": f"late at {p.get('at_stop_name')}",
            "overdue": f"no check-in at {p.get('at_stop_name')}", "not_started": "trip not started",
            "manual": f"reported: {p.get('reason')}",
        }.get(p["source"], p["source"])
        msgs.append(Message(user_ids=admins, type=t, severity=Severity.WARNING if n < 15 else Severity.CRITICAL,
                            title=f"Route {code} +{n} min",
                            body=f"{source_text[:1].upper()}{source_text[1:]}. {len(targets)} students affected."
                                 + ("" if to_riders else " No further alerts will be sent for this trip."),
                            payload={**base, "delay_min": n, "source": p["source"], "affected_students": len(targets)}))
        if to_riders and (p["source"] != "manual" or p.get("reported_by") != p["driver_id"]):
            msgs.append(Message(user_ids=[p["driver_id"]], type=t, severity=Severity.WARNING,
                                title=f"Running {n} min behind schedule",
                                body="Students at upcoming stops have been told. Report a reason if you know it.",
                                payload={**base, "delay_min": n}))
        return msgs

    if t == "ScheduleSkipped":
        when = f"{p['direction']} {str(p['departure_time'])[:5]}"
        return [Message(user_ids=admins, type=t, severity=Severity.WARNING,
                        title=f"Route {code} {when} won't run today",
                        body=f"Bus {p['registration_no']} is {p['bus_status']}. Give the schedule another bus "
                             "and the trip will be created within 15 minutes.",
                        payload={**base, "schedule_id": p["schedule_id"], "bus_id": p["bus_id"]})]

    if t == "TripDelayResolved":
        targets = await delay_student_targets(session, p)
        return [
            Message(user_ids=list(targets), type=t, severity=Severity.INFO,
                    title=f"Route {code} is back on schedule",
                    body="The earlier delay has cleared.", payload=base),
            Message(user_ids=admins, type=t, severity=Severity.INFO,
                    title=f"Route {code} delay cleared",
                    body=f"Now {p['delay_min']} min off schedule (was {p['previous_delay_min']}).", payload=base),
        ]

    if t == "BusApproaching":
        targets = await approach_student_targets(session, p)
        km = f"{p['distance_m'] / 1000:.1f}"
        payload = {**base, "stop": {"stop_id": p["stop_id"], "stop_name": p["stop_name"]},
                   "distance_m": p["distance_m"]}
        if p["direction"] == Direction.PICKUP.value:
            return [Message(user_ids=targets, type=t, severity=Severity.INFO,
                            title=f"Route {code} bus is {km} km from your stop",
                            body=f"Head to {p['stop_name']} now. Expected around {_hm(p['expected_at'])}.",
                            payload=payload)]
        return [Message(user_ids=targets, type=t, severity=Severity.INFO,
                        title=f"{p['stop_name']} is coming up",
                        body=f"About {km} km to go. Get ready to get off.", payload=payload)]

    if t == "TripStarted":
        trip = await trips_service.get_trip(session, p["trip_id"])
        stops_of = await _student_stops(session, trip.route_id)
        sched = {e.stop_id: e.scheduled_at for e in trip.stop_events}
        msgs = []
        for sid, stop_id in stops_of.items():
            at = f" Scheduled at your stop {_hm(sched[stop_id])}." if stop_id in sched else ""
            label = "pickup" if trip.direction == Direction.PICKUP else "drop"
            msgs.append(Message(user_ids=[sid], type=t, severity=Severity.INFO,
                                title=f"Route {code} {label} bus has left",
                                body=f"Departed {_hm(p['started_at'])}.{at}", payload=base))
        return msgs

    if t == "TripCancelled":
        riders = list((await _student_stops(session, p["route_id"])).keys())
        return [Message(user_ids=riders + admins, type=t, severity=Severity.CRITICAL,
                        title=f"Route {code} trip cancelled", body=p.get("reason") or "", payload=base)]

    if t in ("CapacityWarning", "OverCapacity"):
        over = t == "OverCapacity"
        return [Message(
            user_ids=[p["driver_id"], *admins], type=t,
            severity=Severity.CRITICAL if over else Severity.WARNING,
            title=f"Route {code} {'over capacity' if over else 'nearly full'}",
            body=f"{p['boarded']}/{p['capacity']} seats taken ({p['pct']}%).",
            payload={**base, "boarded": p["boarded"], "capacity": p["capacity"]},
        )]

    if t == "UnallocatedBoarding":
        other = p.get("allocated_route_id")
        where = "has no route allocation"
        if other:
            where = f"is allocated to route {(await md_service.get_route(session, other)).code}"
        return [Message(user_ids=[p["driver_id"], *admins], type=t, severity=Severity.WARNING,
                        title=f"Unexpected rider on route {code}",
                        body=f"{p['student_name']} boarded but {where}.",
                        payload={**base, "student_id": p["student_id"]})]

    if t in ("StudentAllocated", "AllocationChanged"):
        stop = await session.get(Stop, p["stop_id"])
        verb = "moved to" if t == "AllocationChanged" else "assigned to"
        return [Message(user_ids=[p["student_id"]], type=t, severity=Severity.INFO,
                        title=f"You're {verb} route {code}",
                        body=f"Your stop: {stop.name if stop else '?'}.", payload=base)]

    if t == "BusDriverAssigned":
        plate = p["registration_no"]
        msgs = []
        if p.get("driver_id"):
            moved = f" You no longer drive bus {p['moved_from_registration_no']}." if p.get("moved_from_bus_id") else ""
            msgs.append(Message(user_ids=[p["driver_id"]], type=t, severity=Severity.INFO,
                                title=f"You're now driving bus {plate}",
                                body=f"Its runs appear under Runs from today.{moved}", payload={"bus_id": p["bus_id"]}))
        if p.get("previous_driver_id"):
            msgs.append(Message(user_ids=[p["previous_driver_id"]], type=t, severity=Severity.INFO,
                                title=f"You're no longer assigned to bus {plate}",
                                body="The transport office has assigned another driver to it.",
                                payload={"bus_id": p["bus_id"]}))
        return msgs

    if t == "AllocationEnded":
        return [Message(user_ids=[p["student_id"]], type=t, severity=Severity.INFO,
                        title="Your bus allocation has ended",
                        body=f"You're no longer allocated to route {code}.", payload=base)]

    # ---- Student reports: payloads carry no student id; the reports module looks it up ----
    report_payload = {"report_id": p.get("report_id"), "trip_id": p.get("trip_id")}
    if t == "ReportAnalysed":
        if not p.get("first"):
            return []  # a re-check doesn't need another alert
        level = {"critical": Severity.CRITICAL, "high": Severity.WARNING}.get(p["severity"], Severity.INFO)
        return [Message(user_ids=admins, type=t, severity=level,
                        title=f"New {_REPORT_KIND.get(p['kind'], 'problem')} report",
                        body=_cut(p.get("summary") or "", 500), payload=report_payload)]

    if t == "ReportFollowUp":
        return [Message(user_ids=admins, type=t, severity=Severity.INFO,
                        title="A student followed up on a report", body="Open Issues to read it.",
                        payload=report_payload)]

    if t in ("ReportReplied", "ReportClosed", "LostItemMatched"):
        student = await reports_service.recipient_id(session, p["report_id"])
        if t == "ReportReplied":
            title, body = "The transport office replied to your report", p["body"]
        elif t == "ReportClosed":
            title, body = "Your report was closed", p.get("note") or "Thanks for letting us know."
        else:
            title = "We may have found your item"
            body = f"Logged as found: \"{p['description']}\". Collect it from the transport office."
        return [Message(user_ids=[student], type=t, severity=Severity.INFO, title=title, body=_cut(body, 500),
                        payload=report_payload)]
    return []


_REPORT_KIND = {"lateness": "late bus", "overcrowding": "overcrowding", "safety": "safety",
                "lost_item": "lost item", "other": "problem"}


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


HANDLED_EVENTS = [
    "TripDelayed", "TripDelayResolved", "TripStarted", "TripCancelled", "BusApproaching", "ScheduleSkipped",
    "CapacityWarning", "OverCapacity", "UnallocatedBoarding",
    "StudentAllocated", "AllocationChanged", "AllocationEnded", "BusDriverAssigned",
    "ReportAnalysed", "ReportFollowUp", "ReportReplied", "ReportClosed", "LostItemMatched",
]
