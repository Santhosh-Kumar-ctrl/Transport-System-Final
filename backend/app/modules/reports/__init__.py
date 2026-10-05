"""Reports module: student problem reports, the AI triage agent, lost and found."""

import asyncio

from fastapi import FastAPI

from app.core import events, tasks
from app.core.db import SessionLocal
from app.core.realtime import hub
from app.core.roles import Role
from app.modules.reports import agent, llm, service
from app.modules.reports.models import FoundItem
from app.modules.reports.router import router

# One analysis at a time: the local model shares one GPU, and each run holds a DB session.
_analysis_slot = asyncio.Semaphore(1)
# Reports queued or being analysed in this process, so the retry loop doesn't run them twice.
_in_flight: set[int] = set()


async def _analyse(report_id: int) -> None:
    _in_flight.add(report_id)
    try:
        async with _analysis_slot, SessionLocal() as session:
            await agent.analyse(session, report_id)
            await session.commit()
    finally:
        _in_flight.discard(report_id)


async def _on_report(ev: events.Event) -> None:
    await _analyse(ev.payload["report_id"])


async def _retry_pending() -> None:
    async with SessionLocal() as session:
        ids = await service.pending_analysis(session)
    for report_id in ids:
        if report_id not in _in_flight:
            await _analyse(report_id)


async def _embed_found_item(ev: events.Event) -> None:
    vector = await llm.embed(ev.payload["description"])
    if vector is None:
        return
    async with SessionLocal() as session:
        item = await session.get(FoundItem, ev.payload["found_item_id"])
        if item:
            item.embedding = vector
            await session.commit()


async def _refresh_admins(ev: events.Event) -> None:
    """Tell open admin screens to refresh. Admins only: report events never go to route topics,
    which other students subscribe to."""
    data = {"event": ev.type, "payload": {"report_id": ev.payload.get("report_id")}, "occurred_at": ev.occurred_at}
    await hub.send_to_role(Role.ADMIN, "ops", data)


REPORT_EVENTS = ["ReportSubmitted", "ReportAnalysed", "ReportFollowUp", "ReportReplied", "ReportClosed",
                 "LostItemMatched", "FoundItemLogged"]


def register(app: FastAPI) -> None:
    events.subscribe("ReportSubmitted", _on_report)
    events.subscribe("ReportAnalysisRequested", _on_report)
    events.subscribe("FoundItemLogged", _embed_found_item)
    for event_type in REPORT_EVENTS:
        events.subscribe(event_type, _refresh_admins)
    tasks.every(60, "report-analyser", _retry_pending)


__all__ = ["router", "register"]
