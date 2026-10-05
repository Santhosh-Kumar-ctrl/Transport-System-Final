"""Delay detection (P0: schedule-based).

Delay = actual time - scheduled time, observed when:
  * the trip starts            (TripStarted)
  * the driver reaches a stop  (StopArrived)
  * the watcher sees the next stop overdue, or a trip that never started
  * a driver/admin reports it manually

Alerting rules (avoid spamming students):
  * alerts go out in steps (DELAY_ALERT_STEPS, default 5, 15, 30, 60 minutes late): TripDelayed
    is raised when the delay reaches a step that hasn't been announced since the trip was last
    on time. Once the last step has been announced the trip raises nothing more, however long
    it stays late (a trip that never starts makes at most one alert per step, not one every
    few minutes all day)
  * steps above DELAY_STUDENT_ALERT_MAX_MIN reach the transport office only
    (`notify_students: false` in the payload), except the first alert of a delay, which riders
    always get
  * raise TripDelayResolved when a stop check-in shows the bus back under the threshold; the
    steps start again from the bottom if it falls behind later
  * manual reports always raise, and always reach riders

Public API: evaluate, current_delays, latest_report.
"""

from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.config import settings
from app.core.deps import Principal
from app.core.errors import Forbidden, InvalidState
from app.core.roles import Role
from app.core.timeutil import minutes_between, now_utc, today_local
from app.modules.delay_monitor.models import DelayReport, DelaySource
from app.modules.delay_monitor.schemas import AffectedStop
from app.modules.trips import service as trips_service
from app.modules.trips.models import Trip, TripStatus


async def latest_report(session: AsyncSession, trip_id: int) -> DelayReport | None:
    return await session.scalar(
        select(DelayReport).where(DelayReport.trip_id == trip_id)
        .order_by(DelayReport.created_at.desc(), DelayReport.id.desc()).limit(1)
    )


async def current_delays(session: AsyncSession, trip_ids: list[int]) -> dict[int, int]:
    """Latest alerted delay per trip (0 once recovered)."""
    if not trip_ids:
        return {}
    rows = await session.execute(
        select(DelayReport.trip_id, DelayReport.delay_min, DelayReport.source)
        .where(DelayReport.trip_id.in_(trip_ids))
        .distinct(DelayReport.trip_id)
        .order_by(DelayReport.trip_id, DelayReport.created_at.desc(), DelayReport.id.desc())
    )
    return {tid: (0 if src == DelaySource.RECOVERED else d) for tid, d, src in rows.all()}


def affected_stops(trip: Trip, delay_min: int) -> list[AffectedStop]:
    """Stops the bus has not reached yet, with their new expected time."""
    if trip.status == TripStatus.SCHEDULED:
        pending = list(trip.stop_events)
    else:
        ns = trips_service.next_stop(trip)
        pending = [e for e in trip.stop_events if ns and e.sequence >= ns.sequence]
    return [
        AffectedStop(sequence=e.sequence, stop_id=e.stop_id, stop_name=e.stop_name,
                     scheduled_at=e.scheduled_at, expected_at=e.scheduled_at + timedelta(minutes=delay_min))
        for e in pending
    ]


async def evaluate(
    session: AsyncSession,
    trip: Trip,
    observed_delay: int,
    source: DelaySource,
    *,
    at_sequence: int | None = None,
    reason: str | None = None,
    reported_by: int | None = None,
) -> DelayReport | None:
    """Apply the alerting rules. Returns the report created, or None if nothing was raised."""
    threshold = settings.delay_threshold_min
    last = await latest_report(session, trip.id)
    in_delayed_state = last is not None and last.source != DelaySource.RECOVERED
    last_delay = await _alerted_delay(session, trip.id) if in_delayed_state else 0

    if observed_delay >= threshold or source == DelaySource.MANUAL:
        manual = source == DelaySource.MANUAL
        step = alert_step(observed_delay)
        if in_delayed_state and not manual and step <= alert_step(last_delay):
            return None  # this step was already announced (or the last step has been reached)
        # Riders always hear the first alert of a delay; later steps above the limit are office-only.
        notify_students = manual or not in_delayed_state or step <= settings.delay_student_alert_max_min
        report = DelayReport(trip_id=trip.id, source=source, delay_min=observed_delay,
                             at_sequence=at_sequence, reason=reason, reported_by=reported_by)
        session.add(report)
        await session.flush()
        at_stop = next((e.stop_name for e in trip.stop_events if e.sequence == at_sequence), None)
        await events.publish(
            session, "TripDelayed",
            {"trip_id": trip.id, "route_id": trip.route_id, "bus_id": trip.bus_id,
             "driver_id": trip.driver_id, "direction": trip.direction.value,
             "service_date": trip.service_date, "delay_min": observed_delay,
             "previous_delay_min": last_delay, "source": source.value, "reason": reason,
             "at_sequence": at_sequence, "at_stop_name": at_stop, "reported_by": reported_by,
             "alert_step": step, "notify_students": notify_students,
             "affected_stops": [s.model_dump() for s in affected_stops(trip, observed_delay)]},
            aggregate=("trip", trip.id), actor_id=reported_by,
        )
        return report

    if in_delayed_state and source == DelaySource.STOP_ARRIVAL:
        report = DelayReport(trip_id=trip.id, source=DelaySource.RECOVERED, delay_min=max(0, observed_delay),
                             at_sequence=at_sequence)
        session.add(report)
        await session.flush()
        await events.publish(
            session, "TripDelayResolved",
            {"trip_id": trip.id, "route_id": trip.route_id, "driver_id": trip.driver_id,
             "direction": trip.direction.value, "delay_min": max(0, observed_delay),
             "previous_delay_min": last_delay, "at_sequence": at_sequence,
             "remaining_stops": [s.model_dump() for s in affected_stops(trip, max(0, observed_delay))]},
            aggregate=("trip", trip.id),
        )
        return report
    return None


def alert_step(delay_min: int) -> int:
    """The highest alert step this delay has reached (0 = under the first step)."""
    return max((s for s in settings.delay_steps if delay_min >= s), default=0)


async def _alerted_delay(session: AsyncSession, trip_id: int) -> int:
    """The largest delay announced for the trip since it was last back on schedule."""
    last_recovery = await session.scalar(
        select(func.max(DelayReport.id)).where(DelayReport.trip_id == trip_id,
                                               DelayReport.source == DelaySource.RECOVERED)
    )
    return await session.scalar(
        select(func.coalesce(func.max(DelayReport.delay_min), 0)).where(
            DelayReport.trip_id == trip_id, DelayReport.source != DelaySource.RECOVERED,
            DelayReport.id > (last_recovery or 0),
        )
    )


async def report_manual(
    session: AsyncSession, trip_id: int, p: Principal, delay_min: int, reason: str
) -> DelayReport:
    trip = await trips_service.get_trip(session, trip_id)
    if p.role == Role.DRIVER and trip.driver_id != p.id:
        raise Forbidden("Only the assigned driver can report a delay on this trip")
    if trip.status not in (TripStatus.SCHEDULED, TripStatus.IN_PROGRESS):
        raise InvalidState("Trip is already finished", code="bad_trip_state")
    ns = trips_service.next_stop(trip) if trip.status == TripStatus.IN_PROGRESS else None
    report = await evaluate(session, trip, delay_min, DelaySource.MANUAL,
                            at_sequence=ns.sequence if ns else None, reason=reason, reported_by=p.id)
    assert report is not None  # manual reports always raise
    return report


async def watch_once(session: AsyncSession) -> int:
    """One pass of the background watcher. Returns how many alerts were raised."""
    threshold = timedelta(minutes=settings.delay_threshold_min)
    now = now_utc()
    raised = 0
    for trip in await trips_service.active_trips(session):
        ns = trips_service.next_stop(trip)
        if ns and now > ns.scheduled_at + threshold:
            if await evaluate(session, trip, minutes_between(now, ns.scheduled_at),
                              DelaySource.OVERDUE, at_sequence=ns.sequence):
                raised += 1
    for trip in await trips_service.list_trips(session, service_date=today_local(), status=TripStatus.SCHEDULED):
        if now > trip.scheduled_departure + threshold:
            if await evaluate(session, trip, minutes_between(now, trip.scheduled_departure),
                              DelaySource.NOT_STARTED, at_sequence=None):
                raised += 1
    return raised


async def list_reports(
    session: AsyncSession, *, trip_id: int | None = None, service_date: date | None = None
) -> list[DelayReport]:
    stmt = select(DelayReport).order_by(DelayReport.created_at.desc())
    if trip_id:
        stmt = stmt.where(DelayReport.trip_id == trip_id)
    if service_date:
        stmt = stmt.join(Trip, Trip.id == DelayReport.trip_id).where(Trip.service_date == service_date)
    return list(await session.scalars(stmt.limit(500)))
