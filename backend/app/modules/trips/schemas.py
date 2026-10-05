from datetime import date, datetime, time

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.core.schemas import PatchModel

from app.modules.trips.models import Direction, TripStatus


class ScheduleIn(BaseModel):
    route_id: int
    bus_id: int
    driver_id: int | None = None  # default: the bus's assigned driver
    direction: Direction
    departure_time: time
    days_of_week: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6], min_length=1)
    is_active: bool = True

    @field_validator("days_of_week")
    @classmethod
    def valid_days(cls, v: list[int]) -> list[int]:
        if any(d < 1 or d > 7 for d in v):
            raise ValueError("days_of_week uses ISO weekdays 1 (Mon) .. 7 (Sun)")
        return sorted(set(v))


class ScheduleUpdate(PatchModel):
    NOT_NULL = ("bus_id", "driver_id", "departure_time", "days_of_week", "is_active")

    bus_id: int | None = None
    driver_id: int | None = None
    departure_time: time | None = None
    days_of_week: list[int] | None = None
    is_active: bool | None = None


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    route_id: int
    bus_id: int
    driver_id: int
    direction: Direction
    departure_time: time
    days_of_week: list[int]
    is_active: bool


class StopEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    route_stop_id: int | None
    stop_id: int
    stop_name: str
    sequence: int
    scheduled_at: datetime
    arrived_at: datetime | None
    delay_min: int | None


class TripOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    schedule_id: int | None
    route_id: int
    bus_id: int
    driver_id: int
    direction: Direction
    service_date: date
    scheduled_departure: datetime
    status: TripStatus
    started_at: datetime | None
    ended_at: datetime | None
    current_delay_min: int
    cancel_reason: str | None


class RouteBrief(BaseModel):
    id: int
    code: str
    name: str
    color: str


class BusBrief(BaseModel):
    id: int
    registration_no: str
    capacity: int


class DriverBrief(BaseModel):
    id: int
    full_name: str
    phone: str | None


class TripDetail(TripOut):
    route: RouteBrief
    bus: BusBrief
    driver: DriverBrief
    stops: list[StopEventOut]
    next_stop: StopEventOut | None


class GenerateIn(BaseModel):
    service_date: date | None = None  # default: today (college-local)


class GenerateOut(BaseModel):
    service_date: date
    created: int
    existing: int
    skipped: int = 0  # schedules whose bus is in maintenance or retired


class ArriveIn(BaseModel):
    # Simulation only (admin, ALLOW_SIMULATION=true): pretend the bus arrived at this time.
    arrived_at: AwareDatetime | None = None


class StartIn(BaseModel):
    started_at: AwareDatetime | None = None  # simulation only, see ArriveIn


class CancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=255)
