"""Facts from the system's own records that support (or don't) a student's report.

Everything here is plain code reading other modules through their services (and, read-only,
the tracking module's position table). The language model never decides a verdict.
"""

import math
import re
from datetime import timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.timeutil import local_tz, minutes_between, now_utc
from app.modules.allocation import service as alloc_service
from app.modules.boarding import service as boarding_service
from app.modules.capacity import service as capacity_service
from app.modules.master_data import service as md_service
from app.modules.reports import llm
from app.modules.reports.models import FoundItem, FoundItemStatus, Report, ReportKind
from app.modules.reports.schemas import Claims, Finding, MatchCandidate
from app.modules.tracking.models import BusPosition
from app.modules.trips import service as trips_service
from app.modules.trips.models import Trip, TripStatus, TripStopEvent

# Checks every report of a kind gets; the model (or the rules) can add more.
DEFAULT_CHECKS = {
    ReportKind.LATENESS: ["on_this_trip", "lateness"],
    ReportKind.OVERCROWDING: ["on_this_trip", "crowding"],
    ReportKind.SAFETY: ["on_this_trip", "speed"],
    ReportKind.LOST_ITEM: ["on_this_trip", "lost_item"],
    ReportKind.OTHER: ["on_this_trip"],
}
ORDER = ["on_this_trip", "trip_ran", "lateness", "stop_reached", "crowding", "speed", "conduct", "lost_item"]


def _hm(dt) -> str:
    return dt.astimezone(local_tz()).strftime("%H:%M")


def plan_checks(kind: ReportKind, claims: Claims, trip: Trip | None) -> list[str]:
    checks = set(DEFAULT_CHECKS[kind]) | set(claims.extra_checks)
    if claims.subtype == "skipped_stop":
        checks.add("stop_reached")
        checks.discard("lateness")  # "on time" would mislead: the question is whether it stopped
    if claims.subtype == "never_came" or (trip and trip.status in (TripStatus.SCHEDULED, TripStatus.CANCELLED)):
        checks.add("trip_ran")
        checks.discard("lateness")
    if claims.subtype in ("harassment", "rude", "accident"):
        checks.add("conduct")
    if claims.subtype == "speeding" or kind == ReportKind.SAFETY:
        checks.add("speed")
    return [c for c in ORDER if c in checks]


def _stop_event(trip: Trip, report: Report, claims: Claims) -> TripStopEvent | None:
    """The stop the report is about: one the student names, else their allocated stop.

    An anonymous report only ever uses a stop the student named: their allocated stop would
    narrow down who sent it."""
    if claims.mentioned_stop:
        want = claims.mentioned_stop.lower().strip()
        for e in trip.stop_events:
            name = e.stop_name.lower()
            if want and (want in name or name in want):
                return e
    if report.anonymous:
        return None
    return next((e for e in trip.stop_events if e.stop_id == report.stop_id), None)


async def gather(session: AsyncSession, report: Report, claims: Claims) -> tuple[list[Finding], list[MatchCandidate]]:
    trip = await trips_service.get_trip(session, report.trip_id) if report.trip_id else None
    findings: list[Finding] = []
    candidates: list[MatchCandidate] = []
    for check in plan_checks(report.kind, claims, trip):
        if check == "lost_item":
            candidates = await lost_item_candidates(session, report, trip)
            findings.append(_lost_item_finding(candidates))
        elif trip is None:
            if check == "on_this_trip":
                findings.append(Finding(check=check, verdict="no_data", detail="The report isn't linked to a trip."))
        elif check == "conduct":
            findings.append(Finding(check=check, verdict="no_data",
                                    detail="Trip records can't show conduct; this needs a staff follow-up."))
        else:
            findings.append(await _CHECKS[check](session, report, claims, trip))
    return findings, candidates


async def _on_this_trip(session, report: Report, claims: Claims, trip: Trip) -> Finding:
    boarding = (await boarding_service.student_boardings_on(session, report.student_id, [trip.id])).get(trip.id)
    if boarding and report.anonymous:
        # No boarding time: matched against the trip roster it would identify the reporter.
        return Finding(check="on_this_trip", verdict="confirmed", detail="The reporter boarded this trip.")
    if boarding:
        return Finding(check="on_this_trip", verdict="confirmed",
                       detail=f"The student boarded this trip at {_hm(boarding.boarded_at)}.",
                       numbers={"boarded_at": boarding.boarded_at.isoformat()})
    allocation = await alloc_service.get_active(session, report.student_id)
    if allocation and allocation.route_id == trip.route_id:
        return Finding(check="on_this_trip", verdict="partly",
                       detail="The student is allocated to this route but didn't scan the boarding QR on this trip.")
    return Finding(check="on_this_trip", verdict="not_supported",
                   detail="The student didn't board this trip and isn't allocated to its route.")


async def _trip_ran(session, report: Report, claims: Claims, trip: Trip) -> Finding:
    dep = _hm(trip.scheduled_departure)
    if trip.status == TripStatus.CANCELLED:
        why = f" Reason given: {trip.cancel_reason}." if trip.cancel_reason else ""
        return Finding(check="trip_ran", verdict="confirmed", detail=f"The {dep} trip was cancelled.{why}")
    if trip.status == TripStatus.SCHEDULED:
        if now_utc() > trip.scheduled_departure + timedelta(minutes=settings.delay_threshold_min):
            late = minutes_between(now_utc(), trip.scheduled_departure)
            return Finding(check="trip_ran", verdict="confirmed",
                           detail=f"The {dep} trip never started ({late} min past departure).",
                           numbers={"minutes_past_departure": late})
        return Finding(check="trip_ran", verdict="no_data", detail=f"The {dep} trip hasn't departed yet.")
    ev = _stop_event(trip, report, claims)
    if ev and ev.arrived_at:
        return Finding(check="trip_ran", verdict="not_supported",
                       detail=f"The bus ran and reached {ev.stop_name} at {_hm(ev.arrived_at)}.",
                       numbers={"arrived_at": ev.arrived_at.isoformat()})
    where = ev.stop_name if ev else "the student's stop"
    return Finding(check="trip_ran", verdict="partly",
                   detail=f"The trip ran (started {_hm(trip.started_at)}) but has no arrival recorded at {where}.")


async def _lateness(session, report: Report, claims: Claims, trip: Trip) -> Finding:
    ev = _stop_event(trip, report, claims)
    threshold = settings.delay_threshold_min
    if ev and ev.arrived_at is not None and ev.delay_min is not None:
        actual, where = ev.delay_min, f"reached {ev.stop_name} at {_hm(ev.arrived_at)}"
    elif ev and trip.status == TripStatus.IN_PROGRESS and now_utc() > ev.scheduled_at:
        actual, where = minutes_between(now_utc(), ev.scheduled_at), f"still hasn't reached {ev.stop_name}"
    elif trip.status in (TripStatus.IN_PROGRESS, TripStatus.COMPLETED):
        actual, where = trip.current_delay_min, "was running"
    else:
        return Finding(check="lateness", verdict="no_data", detail="The trip has no timing data yet.")

    numbers = {"actual_delay_min": actual, "claimed_delay_min": claims.claimed_delay_min,
               "stop": ev.stop_name if ev else None}
    if claims.subtype == "early":
        early = -actual
        verdict = "confirmed" if early >= 2 else "not_supported"
        detail = (f"The bus {where}, {early} min early." if early > 0 else f"The bus {where}, {_late_text(actual)}.")
        return Finding(check="lateness", verdict=verdict, detail=detail, numbers=numbers)

    detail = f"The bus {where}, {_late_text(actual)}."
    claimed = claims.claimed_delay_min
    if actual < threshold:
        verdict = "not_supported"
    elif claimed is None or abs(actual - claimed) <= max(3, round(claimed * 0.25)):
        verdict = "confirmed"
    else:
        verdict = "partly"
        detail += f" The report says about {claimed} min."
    return Finding(check="lateness", verdict=verdict, detail=detail, numbers=numbers)


def _late_text(minutes: int) -> str:
    if minutes > 0:
        return f"{minutes} min late"
    if minutes < 0:
        return f"{-minutes} min early"
    return "on time"


async def _stop_reached(session, report: Report, claims: Claims, trip: Trip) -> Finding:
    ev = _stop_event(trip, report, claims)
    if ev is None:
        return Finding(check="stop_reached", verdict="no_data", detail="Couldn't tell which stop the report is about.")
    if ev.arrived_at is not None:
        return Finding(check="stop_reached", verdict="not_supported",
                       detail=f"The bus checked in at {ev.stop_name} at {_hm(ev.arrived_at)}.")
    later = next((e for e in trip.stop_events if e.sequence > ev.sequence and e.arrived_at), None)
    if later or trip.status == TripStatus.COMPLETED:
        after = f"; it reached {later.stop_name} at {_hm(later.arrived_at)}" if later else ""
        return Finding(check="stop_reached", verdict="confirmed",
                       detail=f"No arrival was recorded at {ev.stop_name}{after}.")
    return Finding(check="stop_reached", verdict="no_data", detail=f"The bus hasn't passed {ev.stop_name} yet.")


async def _crowding(session, report: Report, claims: Claims, trip: Trip) -> Finding:
    occ = await capacity_service.trip_occupancy(session, trip)
    numbers = {"boarded": occ.boarded, "capacity": occ.capacity, "pct": occ.pct}
    detail = f"{occ.boarded} of {occ.capacity} seats were taken ({occ.pct}%) by riders who scanned in."
    if occ.boarded >= occ.capacity:
        verdict = "confirmed"
    elif occ.pct >= settings.capacity_warn_pct:
        verdict = "partly"
    else:
        verdict = "not_supported"
        detail += " Riders who didn't scan aren't counted."
    return Finding(check="crowding", verdict=verdict, detail=detail, numbers=numbers)


async def _speed(session, report: Report, claims: Claims, trip: Trip) -> Finding:
    limit = settings.report_speed_limit_kmph
    row = (await session.execute(
        select(func.count(BusPosition.id), func.max(BusPosition.speed_kmph),
               func.count(BusPosition.id).filter(BusPosition.speed_kmph > limit))
        .where(BusPosition.trip_id == trip.id)
    )).one()
    fixes, top, over = row[0], row[1], row[2]
    if not fixes or top is None:
        return Finding(check="speed", verdict="no_data", detail="There is no GPS speed data for this trip.")
    numbers = {"fixes": fixes, "top_speed_kmph": round(top), "fixes_over_limit": over, "limit_kmph": limit}
    if over >= 2:
        return Finding(check="speed", verdict="confirmed", numbers=numbers,
                       detail=f"GPS shows {over} readings above {limit} km/h, up to {round(top)} km/h.")
    if over == 1:
        return Finding(check="speed", verdict="partly", numbers=numbers,
                       detail=f"GPS shows one reading above {limit} km/h ({round(top)} km/h).")
    return Finding(check="speed", verdict="not_supported", numbers=numbers,
                   detail=f"GPS top speed was {round(top)} km/h, under the {limit} km/h limit.")


_CHECKS = {"on_this_trip": _on_this_trip, "trip_ran": _trip_ran, "lateness": _lateness,
           "stop_reached": _stop_reached, "crowding": _crowding, "speed": _speed}


# ---------------- Lost and found ----------------
_STOPWORDS = {"a", "an", "the", "my", "i", "in", "on", "of", "and", "it", "is", "was", "left", "lost", "bus",
              "seat", "near", "with", "to", "at", "this", "that", "found", "item", "please", "think", "have",
              "forgot", "back", "row", "today", "morning", "evening", "some", "one", "colour", "color"}
EMBED_MIN, WORDS_MIN = 0.6, 0.15


def _words(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", text.lower()) if w not in _STOPWORDS and len(w) > 2}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def lost_item_candidates(session: AsyncSession, report: Report, trip: Trip | None) -> list[MatchCandidate]:
    since = now_utc() - timedelta(days=settings.report_lookback_days)
    stmt = select(FoundItem).where(FoundItem.status == FoundItemStatus.UNCLAIMED, FoundItem.created_at >= since)
    if trip:
        stmt = stmt.where(or_(FoundItem.trip_id == trip.id, FoundItem.bus_id == trip.bus_id))
    items = list(await session.scalars(stmt))
    if not items:
        return []
    report_vec = await llm.embed(report.description)
    report_words = _words(report.description)
    scored = []
    for item in items:
        if report_vec and item.embedding:
            score = _cosine(report_vec, item.embedding)
            ok = score >= EMBED_MIN
        else:
            item_words = _words(item.description)
            union = report_words | item_words
            score = len(report_words & item_words) / len(union) if union else 0.0
            ok = score >= WORDS_MIN
        if ok:
            scored.append((score, item))
    scored.sort(key=lambda s: s[0], reverse=True)
    out = []
    for score, item in scored[:3]:
        bus = await md_service.get_bus(session, item.bus_id) if item.bus_id else None
        out.append(MatchCandidate(found_item_id=item.id, description=item.description, score=round(score, 2),
                                  logged_at=item.created_at, bus_registration_no=bus.registration_no if bus else None))
    return out


def _lost_item_finding(candidates: list[MatchCandidate]) -> Finding:
    if not candidates:
        return Finding(check="lost_item", verdict="no_data",
                       detail="No matching item has been logged as found on this bus yet.")
    best = candidates[0]
    more = f" (+{len(candidates) - 1} other possible)" if len(candidates) > 1 else ""
    return Finding(check="lost_item", verdict="partly",
                   detail=f"A possible match was logged as found: \"{best.description}\"{more}.",
                   numbers={"found_item_ids": [c.found_item_id for c in candidates]})
