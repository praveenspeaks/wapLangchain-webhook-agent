"""events tools available to the support agent."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool
from psycopg.rows import dict_row

from app.database import get_pool

logger = logging.getLogger(__name__)


@tool
async def get_event_tickets(event_name: str) -> str:
    """
    Check ticket availability for events matching the search term(s).
    Searches both event names AND categories.
    Supports multiple keywords separated by commas (e.g., 'tech, yoga, music').

    Args:
        event_name: Keyword(s) to search for. Use commas for multiple terms
                   (e.g., 'tech, yoga', 'music, festival').

    Returns:
        JSON with matching events, tickets sold, and tickets remaining.
    """
    logger.info("get_event_tickets called", extra={"event_name": event_name})

    # Handle multiple keywords separated by commas
    keywords = [k.strip() for k in event_name.split(",") if k.strip()]

    pool = get_pool()
    async with pool.connection() as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]

        all_rows: list[dict[str, Any]] = []
        seen_ids: set[int] = set()

        for keyword in keywords:
            pattern = f"%{keyword}%"
            cur = await conn.execute(
                "SELECT id, event_name, event_date::text, venue,"
                " total_tickets, tickets_sold,"
                " (total_tickets - tickets_sold) AS tickets_remaining,"
                " price::text, category"
                " FROM event_tickets WHERE event_name ILIKE %s OR category ILIKE %s",
                (pattern, pattern),
            )
            rows = await cur.fetchall()

            # Deduplicate by id
            for row in rows:
                if row["id"] not in seen_ids:
                    all_rows.append(row)
                    seen_ids.add(row["id"])

            await cur.close()

    if not all_rows:
        return json.dumps({"error": f"No events found matching '{event_name}'."})

    return json.dumps({"events": all_rows, "total": len(all_rows)})
