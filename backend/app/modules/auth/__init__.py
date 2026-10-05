"""Auth module: users, roles, student/driver profiles, JWT login."""

from fastapi import FastAPI

from app.core import events, realtime
from app.core.db import SessionLocal
from app.core.deps import Principal
from app.modules.auth import service
from app.modules.auth.router import router


async def _session_valid(p: Principal) -> bool:
    async with SessionLocal() as session:
        return await service.session_valid(session, p)


async def _on_sessions_revoked(ev: events.Event) -> None:
    """Password changed or account deactivated: close the user's open sockets now."""
    await realtime.hub.disconnect_user(ev.payload["user_id"])


def register(app: FastAPI) -> None:
    realtime.set_session_check(_session_valid)
    events.subscribe("UserSessionsRevoked", _on_sessions_revoked)


__all__ = ["router", "register"]
