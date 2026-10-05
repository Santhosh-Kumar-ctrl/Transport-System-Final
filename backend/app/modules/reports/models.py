from datetime import datetime
from enum import Enum

from sqlalchemy import ARRAY, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, str_enum


class ReportKind(str, Enum):
    LATENESS = "lateness"  # late, early, skipped stop, never came
    OVERCROWDING = "overcrowding"
    SAFETY = "safety"  # unsafe driving, harassment, accident, conduct
    LOST_ITEM = "lost_item"
    OTHER = "other"


class ReportStatus(str, Enum):
    OPEN = "open"
    REPLIED = "replied"  # staff answered; the student can follow up
    CLOSED = "closed"


class ReportSeverity(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class AnalysisStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class FoundItemStatus(str, Enum):
    UNCLAIMED = "unclaimed"
    MATCHED = "matched"  # linked to a lost-item report, waiting to be collected
    RETURNED = "returned"


class FoundItem(Base, TimestampMixin):
    """Something left on a bus, logged by the driver or the transport office."""

    __tablename__ = "found_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id", ondelete="SET NULL"))
    bus_id: Mapped[int | None] = mapped_column(ForeignKey("buses.id", ondelete="SET NULL"))
    logged_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    description: Mapped[str] = mapped_column(String(300))
    status: Mapped[FoundItemStatus] = mapped_column(
        str_enum(FoundItemStatus, "found_item_status"), default=FoundItemStatus.UNCLAIMED, index=True
    )
    # Text embedding of the description (Ollama), for matching lost-item reports. Null when the
    # model was unavailable; matching then falls back to word overlap.
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(Float))


class Report(Base, TimestampMixin):
    """A problem a student reported, plus the agent's analysis of it."""

    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id", ondelete="SET NULL"))
    route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id", ondelete="SET NULL"))
    stop_id: Mapped[int | None] = mapped_column(ForeignKey("stops.id", ondelete="SET NULL"))
    kind: Mapped[ReportKind] = mapped_column(str_enum(ReportKind, "report_kind"))
    description: Mapped[str] = mapped_column(String(1000))
    # Hide the student's name from staff. The row still records who it was.
    anonymous: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[ReportStatus] = mapped_column(
        str_enum(ReportStatus, "report_status"), default=ReportStatus.OPEN
    )
    severity: Mapped[ReportSeverity | None] = mapped_column(str_enum(ReportSeverity, "report_severity"))

    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        str_enum(AnalysisStatus, "analysis_status"), default=AnalysisStatus.PENDING, index=True
    )
    # {claims, findings, summary, suggested_action, draft_reply, match_candidates, steps}
    analysis: Mapped[dict | None] = mapped_column(JSONB)
    analysed_by: Mapped[str | None] = mapped_column(String(64))  # model name, or "rules"
    analysed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    matched_found_item_id: Mapped[int | None] = mapped_column(ForeignKey("found_items.id", ondelete="SET NULL"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution_note: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (Index("ix_reports_status_created", "status", "created_at"),)


class ReportMessage(Base):
    """The conversation on a report: staff replies and student follow-ups."""

    __tablename__ = "report_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    from_staff: Mapped[bool] = mapped_column(Boolean)
    body: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __mapper_args__ = {"eager_defaults": True}
