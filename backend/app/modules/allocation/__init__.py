"""Allocation module: which route and stop each student rides."""

from fastapi import FastAPI

from app.core import events, realtime
from app.core.db import SessionLocal
from app.core.deps import Principal
from app.modules.allocation import service
from app.modules.allocation.router import router


async def _on_user_deactivated(ev: events.Event) -> None:
    """A deactivated student no longer holds a seat on their route."""
    if ev.payload.get("role") != "student":
        return
    async with SessionLocal() as session:
        if await service.end_for_deactivated(session, ev.payload["user_id"], actor_id=ev.actor_id):
            await session.commit()


async def _may_follow_route(p: Principal, route_id: int) -> bool:
    async with SessionLocal() as session:
        return await service.may_follow_route(session, p, route_id)


def register(app: FastAPI) -> None:
    events.subscribe("UserDeactivated", _on_user_deactivated)
    realtime.set_topic_policy("route", _may_follow_route)


__all__ = ["router", "register"]
