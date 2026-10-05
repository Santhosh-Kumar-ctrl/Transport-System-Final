"""Notifications module: in-app inbox + WebSocket push for operational events."""

from fastapi import FastAPI

from app.core import events, tasks
from app.core.config import settings
from app.core.db import SessionLocal
from app.modules.notifications import service
from app.modules.notifications.router import router


async def _on_event(ev: events.Event) -> None:
    async with SessionLocal() as session:
        messages = await service.messages_for(session, ev)
        await service.deliver(session, messages)


async def _clean_up() -> None:
    async with SessionLocal() as session:
        await service.delete_old_read(session, settings.notification_retention_days)
        await session.commit()


def register(app: FastAPI) -> None:
    for event_type in service.HANDLED_EVENTS:
        events.subscribe(event_type, _on_event)
    tasks.every(6 * 3600, "notification-retention", _clean_up)


__all__ = ["router", "register"]
