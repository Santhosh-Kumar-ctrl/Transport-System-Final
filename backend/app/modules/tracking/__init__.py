"""Tracking module: live bus positions, GPS auto-arrival at stops and "bus is near" alerts."""

from fastapi import FastAPI

from app.core import tasks
from app.core.config import settings
from app.core.db import SessionLocal
from app.modules.tracking import service
from app.modules.tracking.router import router


async def _clean_up() -> None:
    async with SessionLocal() as session:
        await service.delete_old_positions(session, settings.position_retention_days)
        await session.commit()


def register(app: FastAPI) -> None:
    tasks.every(6 * 3600, "position-retention", _clean_up)


__all__ = ["router", "register"]
