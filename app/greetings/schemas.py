"""A month and day are required; the original year is optional."""

from datetime import date
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


class RecipientTimezone(BaseModel):
    timezone: str = Field(description="Recipient's IANA timezone, e.g. Asia/Kolkata")

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Use an IANA timezone such as Asia/Kolkata or Europe/London") from exc
        return value


class OccasionInput(RecipientTimezone):
    """One person can have separate birthday and anniversary records."""

    name: str = Field(min_length=1, max_length=200)
    occasion: Literal["birthday", "anniversary"]
    month: int = Field(ge=1, le=12)
    day: int = Field(ge=1, le=31)
    year: int | None = Field(default=None, ge=1, le=9999)
    country: str = Field(pattern=r"^[A-Z]{2}$", description="ISO country code, e.g. IN or GB")
    phone_number: str = Field(
        pattern=r"^\+[1-9][0-9]{7,14}$",
        description="International phone number including + and country calling code",
    )
    enabled: bool = True

    @model_validator(mode="after")
    def validate_calendar_date(self) -> Self:
        """Use a leap year to allow February 29 when no year is supplied."""
        date(self.year if self.year is not None else 2000, self.month, self.day)
        if not self.name.strip():
            raise ValueError("Name must not be blank")
        self.name = self.name.strip()
        return self
