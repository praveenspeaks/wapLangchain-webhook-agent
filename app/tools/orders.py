"""orders tools available to the support agent."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool
from psycopg.rows import dict_row

from app.database import get_pool

logger = logging.getLogger(__name__)


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
