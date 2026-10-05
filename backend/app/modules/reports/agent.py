"""The report agent: read the claim, gather evidence, set the urgency, draft a reply.

Runs after the report is saved (from the ReportSubmitted / ReportAnalysisRequested subscribers),
never inside the student's request. It only writes its own analysis onto the report: replies,
matches and closing are always an admin's decision.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events
from app.core.timeutil import now_utc
from app.modules.reports import evidence, llm
from app.modules.reports.models import AnalysisStatus, Report, ReportKind, ReportSeverity
from app.modules.reports.schemas import Analysis, Claims, Finding

log = logging.getLogger("transit.reports.agent")

_RANK = [ReportSeverity.LOW, ReportSeverity.NORMAL, ReportSeverity.HIGH, ReportSeverity.CRITICAL]


def _max(a: ReportSeverity, b: ReportSeverity) -> ReportSeverity:
    return a if _RANK.index(a) >= _RANK.index(b) else b


def severity_floor(kind: ReportKind, text: str, claims: Claims, findings: list[Finding]) -> ReportSeverity:
    """The lowest urgency the rules allow. The model can raise it (up to high), never lower it."""
    if kind == ReportKind.SAFETY or claims.subtype in ("harassment", "accident") or llm.has_critical_words(text):
        return ReportSeverity.CRITICAL
    verdicts = {f.check: f.verdict for f in findings}
    if verdicts.get("speed") == "confirmed" or verdicts.get("trip_ran") == "confirmed":
        return ReportSeverity.HIGH
    if kind == ReportKind.LOST_ITEM:
        return ReportSeverity.LOW
    return ReportSeverity.NORMAL


async def analyse(session: AsyncSession, report_id: int) -> Report:
    report = await session.get(Report, report_id)
    first = report.analysed_at is None
    try:
        claims, read_by = await llm.read_claims(report.kind, report.description)
        findings, candidates = await evidence.gather(session, report, claims)
        # The model may raise urgency up to "high". "Critical" (an urgent alert to every admin) comes
        # only from the rules: a safety report, or harassment/accident words in the text.
        hint = claims.severity_hint if claims.severity_hint != ReportSeverity.CRITICAL else ReportSeverity.HIGH
        severity = _max(severity_floor(report.kind, report.description, claims, findings), hint)
        writeup, write_by = await llm.write_up(report.kind, report.description, claims, findings)
    except Exception:  # noqa: BLE001  an unexpected bug must not leave the report pending forever
        log.exception("Analysis of report %s failed", report_id)
        report.analysis_status = AnalysisStatus.FAILED
        await session.flush()
        return report

    report.analysis = Analysis(claims=claims, findings=findings, match_candidates=candidates,
                               summary=writeup.summary, suggested_action=writeup.suggested_action,
                               draft_reply=writeup.draft_reply,
                               steps={"read": read_by, "write": write_by}).model_dump(mode="json")
    report.severity = severity
    report.analysis_status = AnalysisStatus.DONE
    # "qwen3:4b", "rules", or "qwen3:4b+rules" when only one step could use the model.
    report.analysed_by = "+".join(dict.fromkeys((read_by, write_by)))
    report.analysed_at = now_utc()
    await session.flush()

    payload = {"report_id": report.id, "kind": report.kind.value, "severity": severity.value, "first": first,
               "summary": writeup.summary}
    if report.trip_id:
        payload["trip_id"] = report.trip_id
    await events.publish(session, "ReportAnalysed", payload, aggregate=("report", report.id))
    return report
