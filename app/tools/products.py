"""products tools available to the support agent."""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool
from psycopg.rows import dict_row

from app.database import get_pool

logger = logging.getLogger(__name__)


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
