"""Create greeting occasions from chat using the scheduler's validated schema."""

import json
import logging

from langchain_core.tools import tool
from psycopg import Error, errors
from pydantic import ValidationError

from app.config import settings
from app.greetings.repository import GreetingRepository
from app.greetings.schemas import OccasionInput

logger = logging.getLogger(__name__)


@tool
async def add_greeting_occasion(
    name: str | None = None,
    occasion: str | None = None,
    month: int | None = None,
    day: int | None = None,
    country: str | None = None,
    phone_number: str | None = None,
    timezone: str | None = None,
    year: int | None = None,
) -> str:
    """Add a requested birthday/anniversary greeting, or report missing/invalid fields.

    Pass only details supplied by the user across this conversation. Missing fields
    can be omitted. Year is optional; never guess a birth/anniversary year.
    country is a two-letter country code; timezone is the recipient's IANA timezone.
    phone_number must include + and country code. All required details must be valid
    before a record is created. This does not send a message or open a support ticket.
    """
    supplied = {
        "name": name,
        "occasion": occasion,
        "month": month,
        "day": day,
        "country": country.upper() if country else country,
        "phone_number": phone_number,
        "timezone": timezone,
        "year": year,
    }
    try:
        record = OccasionInput.model_validate({k: v for k, v in supplied.items() if v is not None})
    except ValidationError as exc:
        issues = [
            {"field": ".".join(map(str, issue["loc"])) or "date", "message": issue["msg"]}
            for issue in exc.errors(include_input=False, include_url=False)
        ]
        return json.dumps(
            {
                "status": "needs_details",
                "issues": issues,
                "instruction": "Ask for these details; do not open a support ticket. "
                "The original year is optional. No record was saved.",
            }
        )
    try:
        saved = await GreetingRepository().create(record)
    except errors.UniqueViolation:
        return json.dumps(
            {
                "status": "already_exists",
                "message": "This phone number already has this occasion on this month/day. "
                "No duplicate was created and the existing record was not changed.",
            }
        )
    except Error as exc:
        logger.error("Greeting creation failed", extra={"error_type": type(exc).__name__})
        return json.dumps(
            {
                "status": "error",
                "message": "Could not confirm saving the occasion. Do not claim success "
                "or open a support ticket automatically.",
            }
        )
    return json.dumps(
        {
            "status": "created",
            "id": saved["id"],
            "occasion": record.model_dump(mode="json"),
            "automatic_greetings_enabled": settings.greetings_enabled,
            "local_send_time": settings.greetings_time,
        }
    )
