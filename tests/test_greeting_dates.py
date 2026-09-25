"""Validate dates without inventing a year or guessing a phone country code."""

import pytest
from pydantic import ValidationError

from app.greetings.schemas import OccasionInput


def occasion(**changes: object) -> OccasionInput:
    data = {
        "name": "Alex",
        "occasion": "birthday",
        "month": 9,
        "day": 25,
        "country": "GB",
        "timezone": "Europe/London",
        "phone_number": "+447700900123",
    }
    return OccasionInput.model_validate(data | changes)


def test_year_is_optional() -> None:
    assert occasion().year is None
    assert occasion(year=1990).year == 1990


def test_leap_day_without_year_is_valid() -> None:
    assert occasion(month=2, day=29).year is None
    assert occasion(month=2, day=29, year=2000).year == 2000


@pytest.mark.parametrize(
    "changes",
    [
        {"month": 2, "day": 30},
        {"month": 4, "day": 31},
        {"month": 2, "day": 29, "year": 2001},
        {"phone_number": "07700900123"},
        {"name": "  "},
    ],
)
def test_invalid_records_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        occasion(**changes)
