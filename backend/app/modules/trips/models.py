from datetime import date, datetime, time
from enum import Enum

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import Base, TimestampMixin, str_enum


class Direction(str, Enum):
    PICKUP = "pickup"  # stops -> campus (route order)
    DROP = "drop"  # campus -> stops (reverse route order)


class TripStatus(str, Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TripSchedule(Base, TimestampMixin):
    """A recurring service: route + bus + driver + departure time on given weekdays."""

    __tablename__ = "trip_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="RESTRICT"), index=True)
    bus_id: Mapped[int] = mapped_column(ForeignKey("buses.id", ondelete="RESTRICT"))
    driver_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    direction: Mapped[Direction] = mapped_column(str_enum(Direction, "trip_direction"))
    departure_time: Mapped[time] = mapped_column(Time)  # college-local wall clock
    days_of_week: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))  # ISO 1=Mon..7=Sun
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Trip(Base, TimestampMixin):
    """One concrete run of a schedule on a service date."""

    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schedule_id: Mapped[int | None] = mapped_column(ForeignKey("trip_schedules.id", ondelete="SET NULL"))
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id", ondelete="RESTRICT"), index=True)
    bus_id: Mapped[int] = mapped_column(ForeignKey("buses.id", ondelete="RESTRICT"))
    driver_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    direction: Mapped[Direction] = mapped_column(str_enum(Direction, "trip_direction"))
    service_date: Mapped[date] = mapped_column(Date, index=True)
    scheduled_departure: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[TripStatus] = mapped_column(
        str_enum(TripStatus, "trip_status"), default=TripStatus.SCHEDULED, index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Latest observed delay in minutes (from the most recent stop arrival or start).
    current_delay_min: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    cancel_reason: Mapped[str | None] = mapped_column(String(255))

    stop_events: Mapped[list["TripStopEvent"]] = relationship(
        back_populates="trip", order_by="TripStopEvent.sequence",
        cascade="all, delete-orphan", lazy="selectin",
    )

    __table_args__ = (
        UniqueConstraint("schedule_id", "service_date", name="uq_trips_schedule_date"),
        # A driver drives, and a bus runs, at most one trip at a time: enforced by the database so
        # two simultaneous "start" requests can't both win.
        Index("uq_trips_one_running_per_driver", "driver_id", unique=True,
              postgresql_where=text("status = 'in_progress'")),
        Index("uq_trips_one_running_per_bus", "bus_id", unique=True,
              postgresql_where=text("status = 'in_progress'")),
    )


class TripStopEvent(Base):
    """Planned vs actual time at each stop of a trip, in the order the bus visits them.

    Stop identity is snapshotted so history survives later route edits.
    """

    __tablename__ = "trip_stop_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    route_stop_id: Mapped[int | None] = mapped_column(ForeignKey("route_stops.id", ondelete="SET NULL"))
    stop_id: Mapped[int] = mapped_column(ForeignKey("stops.id", ondelete="RESTRICT"))
    stop_name: Mapped[str] = mapped_column(String(120))
    sequence: Mapped[int] = mapped_column(SmallInteger)  # visit order within this trip
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delay_min: Mapped[int | None] = mapped_column(SmallInteger)

    trip: Mapped[Trip] = relationship(back_populates="stop_events")

    __table_args__ = (UniqueConstraint("trip_id", "sequence", name="uq_trip_stop_events_seq"),)

