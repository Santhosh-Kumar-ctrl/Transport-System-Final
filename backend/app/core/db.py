from collections.abc import AsyncIterator

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.errors import Conflict

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request. Routers commit explicitly."""
    async with SessionLocal() as session:
        yield session


async def flush_or_conflict(session: AsyncSession, message: str, code: str | None = None) -> None:
    """Flush; a unique/foreign-key violation becomes a 409 instead of a 500."""
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(message, code=code) from exc
