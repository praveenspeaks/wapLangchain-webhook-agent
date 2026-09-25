"""support tools available to the support agent."""

from __future__ import annotations

import json
import logging
import random

from langchain_core.tools import tool

from app.database import get_pool

logger = logging.getLogger(__name__)


@tool
async def create_support_ticket(issue: str, contact: str) -> str:
    """
    Create a customer support ticket in the database.

    Args:
        issue:   Description of the problem the customer is facing.
        contact: Customer's phone number or email for follow-up.

    Returns:
        JSON with the ticket ID, priority, and estimated response time.
    """
    logger.info("create_support_ticket called", extra={"contact": contact})

    ticket_id = f"TKT-{random.randint(10000, 99999)}"
    urgent_keywords = ("urgent", "broken", "refund", "lost", "damaged")
    priority = "High" if any(kw in issue.lower() for kw in urgent_keywords) else "Normal"

    pool = get_pool()
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO support_tickets"
            " (id, customer_phone, issue, priority, status)"
            " VALUES (%s, %s, %s, %s, 'Open')",
            (ticket_id, contact, issue, priority),
        )

    return json.dumps(
        {
            "ticket_id": ticket_id,
            "priority": priority,
            "status": "Open",
            "estimated_response": (
                "2-4 business hours" if priority == "High" else "24 business hours"
            ),
            "contact": contact,
            "note": "Our support team will reach out to you shortly.",
        }
    )
