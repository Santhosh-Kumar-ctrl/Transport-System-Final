"""In-process domain event bus: the glue between modules.

    await events.publish(session, "TripStarted", {...}, aggregate=("trip", trip.id), actor_id=p.id)
    await session.commit()          # -> event row persisted, subscribers dispatched

* `publish` adds a row to `domain_events` in the caller's transaction. That table
  is the operational history (history module) and the feed Team B's agent reads.
* Subscribers run only **after the transaction commits** (rolled-back work never
  notifies anyone). Each subscriber runs in its own task and must open its own
  session via `SessionLocal()`.
* Subscriber failures are logged and never break the publisher.
* Tests call `await events.drain()` to wait until every cascade of handlers is done.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, event as sa_event, func, literal_column, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.models import Base
from app.core.timeutil import now_utc

log = logging.getLogger("transit.events")


class DomainEvent(Base):
    __tablename__ = "domain_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    aggregate_type: Mapped[str | None] = mapped_column(String(32))
    aggregate_id: Mapped[int | None] = mapped_column(Integer)
    actor_id: Mapped[int | None] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (
        Index("ix_domain_events_aggregate", "aggregate_type", "aggregate_id"),
        # Trip timelines and route history filter on these payload fields; see `payload_text`.
        Index("ix_domain_events_payload_trip_id", text("(payload ->> 'trip_id')")),
        Index("ix_domain_events_payload_route_id", text("(payload ->> 'route_id')")),
    )


def payload_text(key: str):
    """`payload ->> '<key>'` with the key inlined, so it matches the expression indexes above
    (a bound parameter for the key would keep Postgres from using them)."""
    if not key.isidentifier():
        raise ValueError(f"Bad payload key {key!r}")
    return literal_column(f"domain_events.payload ->> '{key}'")


@dataclass(frozen=True)
class Event:
    type: str
    payload: dict[str, Any]
    aggregate_type: str | None = None
    aggregate_id: int | None = None
    actor_id: int | None = None
    occurred_at: datetime = field(default_factory=now_utc)


Handler = Callable[[Event], Awaitable[None]]

_subscribers: dict[str, list[Handler]] = defaultdict(list)
_inflight: set[asyncio.Task] = set()
_PENDING_KEY = "transit_pending_events"


def subscribe(event_type: str, handler: Handler) -> None:
    if handler not in _subscribers[event_type]:
        _subscribers[event_type].append(handler)


def on(event_type: str):
    """Decorator form of `subscribe`, used inside a module's `register()`."""

    def deco(fn: Handler) -> Handler:
        subscribe(event_type, fn)
        return fn

    return deco


def clear_subscribers() -> None:
    _subscribers.clear()


async def publish(
    session: AsyncSession,
    type: str,
    payload: dict[str, Any],
    *,
    aggregate: tuple[str, int] | None = None,
    actor_id: int | None = None,
) -> Event:
    # Normalise once: subscribers see exactly what is stored (ISO strings, not datetimes).
    payload = jsonable(payload)
    ev = Event(
        type=type,
        payload=payload,
        aggregate_type=aggregate[0] if aggregate else None,
        aggregate_id=aggregate[1] if aggregate else None,
        actor_id=actor_id,
    )
    session.add(
        DomainEvent(
            type=ev.type,
            aggregate_type=ev.aggregate_type,
            aggregate_id=ev.aggregate_id,
            actor_id=ev.actor_id,
            payload=payload,
            occurred_at=ev.occurred_at,
        )
    )
    session.sync_session.info.setdefault(_PENDING_KEY, []).append(ev)
    return ev


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):  # date, time
        return value.isoformat()
    if hasattr(value, "value") and not isinstance(value, (int, float, str, bool)):  # Enum
        return value.value
    return value


async def _run(handler: Handler, ev: Event) -> None:
    try:
        await handler(ev)
    except Exception:  # noqa: BLE001 - a broken subscriber must never break others
        log.exception("Event handler %s failed for %s", getattr(handler, "__name__", handler), ev.type)


def _dispatch(ev: Event) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        log.warning("No running loop; dropping dispatch of %s", ev.type)
        return
    for handler in list(_subscribers.get(ev.type, ())):
        task = loop.create_task(_run(handler, ev))
        _inflight.add(task)
        task.add_done_callback(_inflight.discard)


@sa_event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    pending = session.info.pop(_PENDING_KEY, None)
    for ev in pending or ():
        _dispatch(ev)


@sa_event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


async def drain(timeout: float = 10.0) -> None:
    """Wait until all in-flight handlers (including ones they trigger) finish."""
    deadline = asyncio.get_running_loop().time() + timeout
    while _inflight:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Event handlers did not finish in time")
        await asyncio.wait(set(_inflight), timeout=remaining)
