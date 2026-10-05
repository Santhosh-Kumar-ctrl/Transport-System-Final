from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.modules.trips.models import Direction, TripStatus
from app.modules.trips.schemas import RouteBrief


class PositionIn(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    speed_kmph: float | None = Field(default=None, ge=0, le=250)
    heading_deg: float | None = Field(default=None, ge=0, le=360)
    accuracy_m: float | None = Field(default=None, ge=0)
    # When the phone took the fix. Omitted = now. Buffered fixes sent after a network drop keep
    # their real time; anything in the future is treated as now.
    recorded_at: AwareDatetime | None = None  # with a timezone, e.g. ...Z


class PositionBatchIn(BaseModel):
    positions: list[PositionIn] = Field(min_length=1, max_length=500)


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trip_id: int | None
    bus_id: int
    latitude: float
    longitude: float
    speed_kmph: float | None
    heading_deg: float | None
    accuracy_m: float | None
    recorded_at: datetime


class IngestOut(BaseModel):
    accepted: int
    arrived: list[int]  # stop sequences auto-marked as reached by this batch
    approaching: list[int]  # stop sequences whose riders were told the bus is close
    position: PositionOut  # the latest fix of the batch


class LiveStop(BaseModel):
    sequence: int
    stop_id: int
    name: str
    latitude: float | None
    longitude: float | None
    scheduled_at: datetime
    arrived_at: datetime | None


class LiveTrip(BaseModel):
    """Everything a map needs for one trip: the line, the stops and where the bus is."""

    trip_id: int
    route: RouteBrief
    direction: Direction
    status: TripStatus
    bus_registration_no: str
    delay_min: int
    next_stop_sequence: int | None
    position: PositionOut | None
    stops: list[LiveStop]
