from datetime import date

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.roles import Role


class StudentProfileIn(BaseModel):
    roll_no: str = Field(min_length=1, max_length=32)
    department: str | None = None
    year: int | None = Field(default=None, ge=1, le=6)


class DriverProfileIn(BaseModel):
    license_no: str = Field(min_length=1, max_length=32)
    license_expiry: date | None = None


class StudentProfileOut(StudentProfileIn):
    model_config = ConfigDict(from_attributes=True)


class DriverProfileOut(DriverProfileIn):
    model_config = ConfigDict(from_attributes=True)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    phone: str | None
    role: Role
    is_active: bool
    student: StudentProfileOut | None = None
    driver: DriverProfileOut | None = None


class UserBrief(BaseModel):
    """Small projection other modules embed in their responses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    role: Role
    phone: str | None = None
    roll_no: str | None = None


class PasswordInput(BaseModel):
    @field_validator("password", check_fields=False)
    @classmethod
    def password_byte_limit(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value


class UserCreate(PasswordInput):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    role: Role
    student: StudentProfileIn | None = None
    driver: DriverProfileIn | None = None

    @model_validator(mode="after")
    def profile_matches_role(self) -> "UserCreate":
        if self.role == Role.STUDENT and self.student is None:
            raise ValueError("student profile is required for role=student")
        if self.role == Role.DRIVER and self.driver is None:
            raise ValueError("driver profile is required for role=driver")
        if self.role != Role.STUDENT and self.student is not None:
            raise ValueError("student profile only allowed for role=student")
        if self.role != Role.DRIVER and self.driver is not None:
            raise ValueError("driver profile only allowed for role=driver")
        return self


class UserUpdate(PasswordInput):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    student: StudentProfileIn | None = None
    driver: DriverProfileIn | None = None


class LoginIn(PasswordInput):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str = Field(min_length=1, max_length=4096)
    token_type: str = "bearer"
    user: UserOut
