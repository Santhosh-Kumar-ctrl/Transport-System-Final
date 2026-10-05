from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.schemas import PatchModel

from app.modules.master_data.models import BusStatus

HEX_COLOR = r"^#[0-9A-Fa-f]{6}$"


# ---- Buses ----
class BusIn(BaseModel):
    registration_no: str = Field(min_length=2, max_length=20)
    capacity: int = Field(ge=1, le=120)
    model: str | None = Field(default=None, max_length=80)
    status: BusStatus = BusStatus.ACTIVE

    @field_validator("registration_no")
    @classmethod
    def normalise_reg(cls, v: str) -> str:
        return v.strip().upper()


class BusUpdate(PatchModel):
    NOT_NULL = ("registration_no", "capacity", "status")

    registration_no: str | None = Field(default=None, min_length=2, max_length=20)
    capacity: int | None = Field(default=None, ge=1, le=120)
    model: str | None = None
    status: BusStatus | None = None


class BusOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    registration_no: str
    capacity: int
    model: str | None
    status: BusStatus
    driver_id: int | None = None
    driver_name: str | None = None


class AssignDriverIn(BaseModel):
    driver_id: int | None  # null = remove the bus's driver
    # The driver already drives another bus: move them here (that bus is left without a driver).
    move: bool = False


# ---- Stops ----
class StopIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    landmark: str | None = Field(default=None, max_length=160)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class StopUpdate(PatchModel):
    NOT_NULL = ("name",)

    name: str | None = Field(default=None, min_length=1, max_length=120)
    landmark: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class StopOut(StopIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


# ---- Routes ----
class RouteIn(BaseModel):
    code: str = Field(min_length=1, max_length=10)
    name: str = Field(min_length=1, max_length=120)
    color: str = Field(pattern=HEX_COLOR)
    description: str | None = Field(default=None, max_length=255)
    is_active: bool = True


class RouteUpdate(PatchModel):
    NOT_NULL = ("code", "name", "color", "is_active")

    code: str | None = Field(default=None, min_length=1, max_length=10)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    color: str | None = Field(default=None, pattern=HEX_COLOR)
    description: str | None = None
    is_active: bool | None = None


class RouteStopIn(BaseModel):
    stop_id: int
    offset_min: int = Field(ge=0, le=600)


class RouteStopOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    stop_id: int
    sequence: int
    offset_min: int
    stop: StopOut


class RouteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    color: str
    description: str | None
    is_active: bool


class RouteDetail(RouteOut):
    stops: list[RouteStopOut]
