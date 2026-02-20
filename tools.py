"""
tools.py
--------
LangChain tool definitions backed by real PostgreSQL queries.

Each tool acquires a connection from the pool in db.py, executes a query,
and returns a JSON-serialised string for the LLM to humanise.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import UTC, datetime
from typing import Any

from langchain_core.tools import tool
from psycopg.rows import dict_row

from db import get_pool

logger = logging.getLogger(__name__)


# =============================================================================
# Order Management
# =============================================================================


@tool
async def get_order_status(order_id: str) -> str:
    """
    Check the current status of a customer order including its items.

    Args:
        order_id: The order identifier (e.g. ORD-10001).

    Returns:
        JSON string with order details and line items, or an error.
    """
    logger.info("get_order_status called", extra={"order_id": order_id})
    order_id = order_id.strip().upper()

    if not order_id.startswith("ORD"):
        return json.dumps({"error": f"Order {order_id!r} not found. Use format ORD-XXXXX."})

    pool = get_pool()
    async with pool.connection() as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]

        cur = await conn.execute(
            "SELECT id, customer_phone, status, total_amount::text,"
            " created_at::text FROM orders WHERE id = %s",
            (order_id,),
        )
        order: Any = await cur.fetchone()

        if not order:
            return json.dumps({"error": f"Order {order_id!r} not found."})

        items_cur = await conn.execute(
            "SELECT p.name, oi.quantity, oi.unit_price::text "
            "FROM order_items oi JOIN products p ON p.id = oi.product_id "
            "WHERE oi.order_id = %s",
            (order_id,),
        )
        items: Any = await items_cur.fetchall()

    return json.dumps(
        {
            "order_id": order["id"],
            "status": order["status"],
            "total_amount": order["total_amount"],
            "created_at": order["created_at"],
            "items": [
                {"product": i["name"], "qty": i["quantity"], "price": i["unit_price"]}
                for i in items
            ],
        }
    )


@tool
async def get_orders_by_status(status: str) -> str:
    """
    List orders filtered by status.

    Args:
        status: One of pending, paid, shipped, delivered, cancelled.

    Returns:
        JSON list of matching orders (max 10).
    """
    logger.info("get_orders_by_status called", extra={"status": status})
    status = status.strip().lower()
    valid = ("pending", "paid", "shipped", "delivered", "cancelled")

    if status not in valid:
        return json.dumps({"error": f"Invalid status. Choose from: {', '.join(valid)}"})

    pool = get_pool()
    async with pool.connection() as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        cur = await conn.execute(
            "SELECT id, customer_phone, status, total_amount::text,"
            " created_at::text FROM orders"
            " WHERE status = %s ORDER BY created_at DESC LIMIT 10",
            (status,),
        )
        rows = await cur.fetchall()

    return json.dumps({"status": status, "count": len(rows), "orders": rows})


# =============================================================================
# Product Catalog
# =============================================================================


@tool
async def search_product(query: str) -> str:
    """
    Search the product catalog by keyword (matches name or category).

    Args:
        query: Search term (product name, category, etc.).

    Returns:
        JSON list of up to 5 matching products.
    """
    logger.info("search_product called", extra={"query": query})
    pattern = f"%{query.strip()}%"

    pool = get_pool()
    async with pool.connection() as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        cur = await conn.execute(
            "SELECT id, name, description, price::text, stock, category "
            "FROM products WHERE name ILIKE %s OR category ILIKE %s LIMIT 5",
            (pattern, pattern),
        )
        rows = await cur.fetchall()

    if not rows:
        return json.dumps({"results": [], "total": 0, "message": "No products found."})

    return json.dumps({"results": rows, "total": len(rows)})


@tool
async def get_product_info(product_id: int) -> str:
    """
    Get detailed information about a specific product.

    Args:
        product_id: The numeric product ID.

    Returns:
        JSON with full product details or an error.
    """
    logger.info("get_product_info called", extra={"product_id": product_id})

    pool = get_pool()
    async with pool.connection() as conn:
        conn.row_factory = dict_row  # type: ignore[assignment]
        cur = await conn.execute(
            "SELECT id, name, description, price::text, stock, category,"
            " created_at::text FROM products WHERE id = %s",
            (product_id,),
        )
        row: Any = await cur.fetchone()

    if not row:
        return json.dumps({"error": f"Product ID {product_id} not found."})

    row["in_stock"] = row["stock"] > 0
    return json.dumps(row)


# =============================================================================
# Event Tickets
# =============================================================================


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


# =============================================================================
# Support Tickets
# =============================================================================


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


# =============================================================================
# Business Information (no DB needed)
# =============================================================================


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


# =============================================================================
# Tool registry  (imported by agent.py)
# =============================================================================

TOOLS = [
    get_order_status,
    get_orders_by_status,
    search_product,
    get_product_info,
    get_event_tickets,
    create_support_ticket,
    get_business_hours,
]
