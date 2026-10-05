"""Boarding module: rotating trip QR, student check-in, roster, attendance."""

from fastapi import FastAPI

from app.core import events, tasks
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.realtime import hub
from app.modules.boarding import service
from app.modules.boarding.router import router


async def _push_boarding(ev: events.Event) -> None:
    """Live update for the driver's boarded list / seat count. Only the driver hears who boarded;
    the trip's topic (followed by its riders) gets the count."""
    data = {k: ev.payload[k] for k in
            ("trip_id", "student_id", "student_name", "allocation_match", "method", "boarded_count")}
    await hub.send_to_user(ev.payload["driver_id"], "boarding", data)
    await hub.send_to_topic(f"trip:{ev.payload['trip_id']}", "boarding",
                            {"trip_id": data["trip_id"], "boarded_count": data["boarded_count"]})


async def _finalize(ev: events.Event) -> None:
    async with SessionLocal() as session:
        await service.finalize_attendance(session, ev.payload["trip_id"])
        await session.commit()


async def _finalize_cancelled(ev: events.Event) -> None:
    """A run cancelled after it set off (a breakdown) still records who was on the bus."""
    if ev.payload.get("was_running"):
        await _finalize(ev)


async def _reconcile() -> None:
    async with SessionLocal() as session:
        await service.reconcile_attendance(session)
        await session.commit()


def register(app: FastAPI) -> None:
    events.subscribe("StudentBoarded", _push_boarding)
    events.subscribe("TripEnded", _finalize)
    events.subscribe("TripCancelled", _finalize_cancelled)
    tasks.every(settings.attendance_reconcile_interval_seconds, "attendance-reconciler", _reconcile)


__all__ = ["router", "register"]
