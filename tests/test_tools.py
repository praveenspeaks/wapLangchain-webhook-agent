"""
tests/test_tools.py
-------------------
Unit tests for agent tools.
Database calls are mocked via ``db.get_pool`` so tests run offline.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools import (
    create_support_ticket,
    get_business_hours,
    get_event_tickets,
    get_order_status,
    get_orders_by_status,
    get_product_info,
    search_product,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_pool(
    *,
    fetchone: dict[str, Any] | None = None,
    fetchall: list[dict[str, Any]] | None = None,
    side_effect: list[Any] | None = None,
) -> MagicMock:
    """Build a mock pool whose connection returns the given rows."""
    if side_effect:
        # Multiple execute calls return different cursors
        cursors = []
        for spec in side_effect:
            cur = AsyncMock()
            if isinstance(spec, dict):
                cur.fetchone = AsyncMock(return_value=spec)
            else:
                cur.fetchall = AsyncMock(return_value=spec)
            cursors.append(cur)
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=cursors)
    else:
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=fetchone)
        mock_cursor.fetchall = AsyncMock(return_value=fetchall or [])
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_cursor)

    mock_pool = MagicMock()
    mock_pool.connection.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    return mock_pool


# =============================================================================
# get_order_status
# =============================================================================


class TestGetOrderStatus:
    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_valid_order_returns_details(self, mock_gp: MagicMock) -> None:
        order_row = {
            "id": "ORD-10001",
            "customer_phone": "155",
            "status": "pending",
            "total_amount": "129.98",
            "created_at": "2026-02-16",
        }
        items = [{"name": "Headphones", "quantity": 1, "unit_price": "79.99"}]
        mock_gp.return_value = _mock_pool(side_effect=[order_row, items])

        result = json.loads(await get_order_status.ainvoke({"order_id": "ORD-10001"}))
        assert result["order_id"] == "ORD-10001"
        assert result["status"] == "pending"
        assert len(result["items"]) == 1

    @pytest.mark.asyncio
    async def test_invalid_format_returns_error(self) -> None:
        result = json.loads(await get_order_status.ainvoke({"order_id": "INVALID"}))
        assert "error" in result

    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_not_found_returns_error(self, mock_gp: MagicMock) -> None:
        mock_gp.return_value = _mock_pool(fetchone=None)
        result = json.loads(await get_order_status.ainvoke({"order_id": "ORD-99999"}))
        assert "error" in result


# =============================================================================
# get_orders_by_status
# =============================================================================


class TestGetOrdersByStatus:
    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_valid_status_returns_orders(self, mock_gp: MagicMock) -> None:
        rows = [
            {
                "id": "ORD-10001",
                "customer_phone": "155",
                "status": "pending",
                "total_amount": "129.98",
                "created_at": "2026-02-16",
            },
        ]
        mock_gp.return_value = _mock_pool(fetchall=rows)
        result = json.loads(await get_orders_by_status.ainvoke({"status": "pending"}))
        assert result["status"] == "pending"
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_invalid_status_returns_error(self) -> None:
        result = json.loads(await get_orders_by_status.ainvoke({"status": "unknown"}))
        assert "error" in result


# =============================================================================
# search_product
# =============================================================================


class TestSearchProduct:
    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_matching_query_returns_results(self, mock_gp: MagicMock) -> None:
        rows = [
            {
                "id": 1,
                "name": "Wireless Headphones",
                "description": "...",
                "price": "79.99",
                "stock": 23,
                "category": "Electronics",
            }
        ]
        mock_gp.return_value = _mock_pool(fetchall=rows)

        result = json.loads(await search_product.ainvoke({"query": "headphones"}))
        assert result["total"] == 1
        assert result["results"][0]["name"] == "Wireless Headphones"

    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_no_results_returns_zero(self, mock_gp: MagicMock) -> None:
        mock_gp.return_value = _mock_pool(fetchall=[])
        result = json.loads(await search_product.ainvoke({"query": "xyzzy_nonexistent"}))
        assert result["total"] == 0


# =============================================================================
# get_product_info
# =============================================================================


class TestGetProductInfo:
    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_found_product(self, mock_gp: MagicMock) -> None:
        row = {
            "id": 1,
            "name": "Headphones",
            "description": "Nice",
            "price": "79.99",
            "stock": 23,
            "category": "Electronics",
            "created_at": "2026-01-01",
        }
        mock_gp.return_value = _mock_pool(fetchone=row)
        result = json.loads(await get_product_info.ainvoke({"product_id": 1}))
        assert result["name"] == "Headphones"
        assert result["in_stock"] is True

    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_not_found(self, mock_gp: MagicMock) -> None:
        mock_gp.return_value = _mock_pool(fetchone=None)
        result = json.loads(await get_product_info.ainvoke({"product_id": 999}))
        assert "error" in result


# =============================================================================
# get_event_tickets
# =============================================================================


class TestGetEventTickets:
    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_found_event(self, mock_gp: MagicMock) -> None:
        rows = [
            {
                "id": 1,
                "event_name": "Tech Summit",
                "event_date": "2026-04-15",
                "venue": "Hall A",
                "total_tickets": 500,
                "tickets_sold": 347,
                "tickets_remaining": 153,
                "price": "149.99",
                "category": "Conference",
            }
        ]
        mock_gp.return_value = _mock_pool(fetchall=rows)
        result = json.loads(await get_event_tickets.ainvoke({"event_name": "Tech"}))
        assert result["total"] == 1
        assert result["events"][0]["tickets_remaining"] == 153

    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_not_found(self, mock_gp: MagicMock) -> None:
        mock_gp.return_value = _mock_pool(fetchall=[])
        result = json.loads(await get_event_tickets.ainvoke({"event_name": "nonexistent"}))
        assert "error" in result


# =============================================================================
# create_support_ticket
# =============================================================================


class TestCreateSupportTicket:
    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_ticket_created(self, mock_gp: MagicMock) -> None:
        mock_gp.return_value = _mock_pool()
        result = json.loads(
            await create_support_ticket.ainvoke(
                {"issue": "My order is broken", "contact": "+15551234567"}
            )
        )
        assert result["ticket_id"].startswith("TKT-")
        assert result["status"] == "Open"

    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_urgent_gets_high_priority(self, mock_gp: MagicMock) -> None:
        mock_gp.return_value = _mock_pool()
        result = json.loads(
            await create_support_ticket.ainvoke(
                {"issue": "URGENT: refund needed", "contact": "test@example.com"}
            )
        )
        assert result["priority"] == "High"

    @pytest.mark.asyncio
    @patch("tools.get_pool")
    async def test_normal_gets_normal_priority(self, mock_gp: MagicMock) -> None:
        mock_gp.return_value = _mock_pool()
        result = json.loads(
            await create_support_ticket.ainvoke(
                {"issue": "Change delivery address", "contact": "u@example.com"}
            )
        )
        assert result["priority"] == "Normal"


# =============================================================================
# get_business_hours (no DB, no mocking needed)
# =============================================================================


class TestGetBusinessHours:
    @pytest.mark.asyncio
    async def test_returns_schedule(self) -> None:
        result = json.loads(await get_business_hours.ainvoke({}))
        assert "schedule" in result
        assert "currently_open" in result
        assert isinstance(result["currently_open"], bool)

    @pytest.mark.asyncio
    async def test_schedule_contains_weekdays(self) -> None:
        result = json.loads(await get_business_hours.ainvoke({}))
        assert "Monday-Friday" in result["schedule"]
