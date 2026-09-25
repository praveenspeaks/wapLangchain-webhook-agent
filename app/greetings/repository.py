"""PostgreSQL records and atomic claims shared across scheduler processes."""

from datetime import date
from typing import Any, Literal

from psycopg.rows import dict_row

from app.database import get_pool
from app.greetings.schemas import OccasionInput


class GreetingRepository:
    async def check_schema(self) -> None:
        async with get_pool().connection() as conn:
            await conn.execute("SELECT id, timezone FROM greeting_occasions LIMIT 0")
            await conn.execute("SELECT id FROM greeting_deliveries LIMIT 0")

    async def timezones(self) -> list[str]:
        async with get_pool().connection() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT timezone FROM greeting_occasions "
                "WHERE enabled AND timezone IS NOT NULL ORDER BY timezone"
            )
            return [row[0] for row in await cur.fetchall()]

    async def due(self, today: date, timezone: str) -> list[dict[str, Any]]:
        async with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM greeting_occasions WHERE enabled AND month = %s AND day = %s "
                "AND (year IS NULL OR year <= %s) AND timezone = %s ORDER BY id",
                (today.month, today.day, today.year, timezone),
            )
            return list(await cur.fetchall())

    async def claim(self, occasion_id: int, today: date, timezone: str) -> int | None:
        """Commit a claim before sending. Never automatically reclaim an uncertain send."""
        async with get_pool().connection() as conn:
            cur = await conn.execute(
                "INSERT INTO greeting_deliveries "
                "(occasion_id, occurrence_year, scheduled_date, status, attempted_at) "
                "SELECT id, %s, %s, 'sending', now() FROM greeting_occasions "
                "WHERE id = %s AND enabled AND month = %s AND day = %s "
                "AND (year IS NULL OR year <= %s) AND timezone = %s "
                "ON CONFLICT (occasion_id, occurrence_year) DO NOTHING RETURNING id",
                (today.year, today, occasion_id, today.month, today.day, today.year, timezone),
            )
            row = await cur.fetchone()
            return row[0] if row else None

    async def finish(
        self, delivery_id: int, status: Literal["sent", "failed", "unknown"], message_id: str | None
    ) -> None:
        async with get_pool().connection() as conn:
            await conn.execute(
                "UPDATE greeting_deliveries SET status = %s, provider_message_id = %s, "
                "sent_at = CASE WHEN %s = 'sent' THEN now() ELSE NULL END "
                "WHERE id = %s AND status = 'sending'",
                (status, message_id, status, delivery_id),
            )

    async def create(self, occasion: OccasionInput) -> dict[str, Any]:
        async with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "INSERT INTO greeting_occasions "
                "(name, occasion, month, day, year, country, phone_number, enabled, timezone) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    occasion.name,
                    occasion.occasion,
                    occasion.month,
                    occasion.day,
                    occasion.year,
                    occasion.country,
                    occasion.phone_number,
                    occasion.enabled,
                    occasion.timezone,
                ),
            )
            row = await cur.fetchone()
            assert row is not None
            return row

    async def list_occasions(self, limit: int, offset: int) -> list[dict[str, Any]]:
        async with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM greeting_occasions ORDER BY id LIMIT %s OFFSET %s", (limit, offset)
            )
            return list(await cur.fetchall())

    async def set_enabled(self, occasion_id: int, enabled: bool) -> bool:
        async with get_pool().connection() as conn:
            cur = await conn.execute(
                "UPDATE greeting_occasions SET enabled = %s WHERE id = %s RETURNING id",
                (enabled, occasion_id),
            )
            return await cur.fetchone() is not None

    async def set_timezone(self, occasion_id: int, timezone: str) -> bool:
        async with get_pool().connection() as conn:
            cur = await conn.execute(
                "UPDATE greeting_occasions SET timezone = %s WHERE id = %s RETURNING id",
                (timezone, occasion_id),
            )
            return await cur.fetchone() is not None

    async def list_deliveries(self, limit: int, offset: int) -> list[dict[str, Any]]:
        async with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT * FROM greeting_deliveries ORDER BY id DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
            return list(await cur.fetchall())
