"""Trips module: schedules, daily trip generation, start/arrive/end workflow."""

from fastapi import FastAPI

from app.core import events, realtime, tasks
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.deps import Principal
from app.core.errors import NotFound
from app.modules.trips.router import router
from app.modules.trips.service import (
    can_view_trip,
    close_stale_trips,
    generate_trips,
    get_trip,
    reassign_bus_driver,
)


async def _generate_today() -> None:
    async with SessionLocal() as session:
        await generate_trips(session)
        await session.commit()


async def _close_stale() -> None:
    async with SessionLocal() as session:
        await close_stale_trips(session)
        await session.commit()


async def _on_bus_driver_assigned(ev: events.Event) -> None:
    """The bus's new regular driver takes over its schedules and upcoming trips."""
    if ev.payload["driver_id"] is None:
        return  # driver removed: existing runs keep their driver until someone is assigned
    async with SessionLocal() as session:
        await reassign_bus_driver(session, ev.payload["bus_id"], ev.payload["driver_id"], actor_id=ev.actor_id)
        await session.commit()


async def _may_follow_trip(p: Principal, trip_id: int) -> bool:
    """WebSocket "trip:<id>" topics: the same people who may read the trip."""
    async with SessionLocal() as session:
        try:
            trip = await get_trip(session, trip_id)
        except NotFound:
            return False
        return await can_view_trip(session, p, trip)


def register(app: FastAPI) -> None:
    # Ensures today's trips exist at startup and keeps checking (covers midnight rollover).
    tasks.every(settings.trip_generation_interval_seconds, "trip-generator", _generate_today)
    tasks.every(settings.trip_generation_interval_seconds, "stale-trip-closer", _close_stale)
    events.subscribe("BusDriverAssigned", _on_bus_driver_assigned)
    realtime.set_topic_policy("trip", _may_follow_trip)


__all__ = ["router", "register"]
