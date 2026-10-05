"""Shared pydantic building blocks."""

from typing import Any, ClassVar

from pydantic import BaseModel, model_validator


class PatchModel(BaseModel):
    """Partial update body: a field left out stays as it is.

    Fields listed in NOT_NULL are required columns, so an explicit `null` for them is a 422
    validation error instead of a database error.
    """

    NOT_NULL: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="before")
    @classmethod
    def _reject_nulls(cls, data: Any) -> Any:
        if isinstance(data, dict):
            bad = [f for f in cls.NOT_NULL if f in data and data[f] is None]
            if bad:
                raise ValueError(f"{', '.join(bad)} can't be null")
        return data
