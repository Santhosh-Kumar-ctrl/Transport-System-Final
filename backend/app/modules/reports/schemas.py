from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.modules.reports.models import (
    AnalysisStatus,
    FoundItemStatus,
    ReportKind,
    ReportSeverity,
    ReportStatus,
)
from app.modules.trips.models import Direction, TripStatus

Verdict = Literal["confirmed", "partly", "not_supported", "no_data"]
Check = Literal["on_this_trip", "lateness", "stop_reached", "trip_ran", "crowding", "speed", "conduct", "lost_item"]
Subtype = Literal["late", "early", "skipped_stop", "never_came", "crowding", "speeding", "harassment",
                  "rude", "accident", "lost_item", "other"]


# ---------------- Input ----------------
class ReportIn(BaseModel):
    kind: ReportKind
    description: str = Field(min_length=5, max_length=1000)
    trip_id: int | None = None
    anonymous: bool = False


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class CloseIn(BaseModel):
    note: str | None = Field(default=None, max_length=255)


class FoundItemIn(BaseModel):
    trip_id: int | None = None
    description: str = Field(min_length=3, max_length=300)


class FoundItemUpdate(BaseModel):
    status: FoundItemStatus


# ---------------- Agent ----------------
class Claims(BaseModel):
    """What the report says, read from the student's text (by the model or the rules)."""

    subtype: Subtype = "other"
    claimed_delay_min: int | None = Field(default=None, ge=0, le=600)
    mentioned_stop: str | None = Field(default=None, max_length=120)
    extra_checks: list[Check] = []
    severity_hint: ReportSeverity = ReportSeverity.NORMAL


class Finding(BaseModel):
    """One fact from the system's own data, and whether it supports the report."""

    check: Check
    verdict: Verdict
    detail: str
    numbers: dict = {}


class MatchCandidate(BaseModel):
    found_item_id: int
    description: str
    score: float
    logged_at: datetime
    bus_registration_no: str | None = None


class Writeup(BaseModel):
    summary: str = Field(max_length=400)
    suggested_action: str = Field(max_length=400)
    draft_reply: str = Field(max_length=1000)


class Analysis(BaseModel):
    claims: Claims
    findings: list[Finding]
    match_candidates: list[MatchCandidate] = []
    summary: str
    suggested_action: str
    draft_reply: str
    steps: dict[str, str]  # which engine did each step: {"read": "qwen3:4b" | "rules", "write": ...}


# ---------------- Output ----------------
class TripOption(BaseModel):
    trip_id: int
    service_date: date
    direction: Direction
    status: TripStatus
    scheduled_departure: datetime
    route_code: str
    route_color: str
    boarded: bool


class MessageOut(BaseModel):
    id: int
    from_staff: bool
    body: str
    created_at: datetime


class ReportOut(BaseModel):
    """What the student sees. Admins get ReportAdminOut."""

    id: int
    kind: ReportKind
    description: str
    status: ReportStatus
    anonymous: bool
    trip_id: int | None
    service_date: date | None
    direction: Direction | None
    route_code: str | None
    route_color: str | None
    stop_name: str | None
    created_at: datetime
    updated_at: datetime
    resolution_note: str | None
    messages: list[MessageOut] = []


class ReportAdminOut(ReportOut):
    student_id: int | None  # null when the report is anonymous
    student_name: str | None
    roll_no: str | None
    bus_registration_no: str | None
    severity: ReportSeverity | None
    analysis_status: AnalysisStatus
    analysis: Analysis | None
    analysed_by: str | None
    analysed_at: datetime | None
    matched_found_item_id: int | None
    closed_at: datetime | None


class FoundItemOut(BaseModel):
    id: int
    description: str
    status: FoundItemStatus
    trip_id: int | None
    route_code: str | None
    bus_registration_no: str | None
    logged_by_name: str | None
    created_at: datetime
