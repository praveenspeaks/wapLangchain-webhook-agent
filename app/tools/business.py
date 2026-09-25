"""business tools available to the support agent."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
async def get_business_hours() -> str:
    """
    Return the business's current operating hours and open/closed status.

    Returns:
        JSON with schedule and whether the business is currently open.
    """
    now = datetime.now(tz=UTC)
    local_hour = now.hour
    weekday = now.weekday()

    schedule = {
        "Monday-Friday": "09:00 - 18:00 UTC",
        "Saturday": "10:00 - 15:00 UTC",
        "Sunday": "Closed",
        "Public Holidays": "Closed",
    }

    if weekday <= 4:  # Monday-Friday
        is_open = 9 <= local_hour < 18
    elif weekday == 5:  # Saturday
        is_open = 10 <= local_hour < 15
    else:  # Sunday
        is_open = False

    return json.dumps(
        {
            "schedule": schedule,
            "currently_open": is_open,
            "status": ("We are OPEN right now!" if is_open else "We are currently CLOSED."),
            "timezone": "UTC",
        }
    )
