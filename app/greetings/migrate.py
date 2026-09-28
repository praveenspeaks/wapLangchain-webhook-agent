"""Apply the additive migrations: automatically at startup, or python -m app.greetings.migrate."""

import logging
from pathlib import Path
from typing import LiteralString, cast

import psycopg
from psycopg import AsyncConnection

from app.config import settings

logger = logging.getLogger(__name__)
DIRECTORY = Path(__file__).resolve().parents[2] / "migrations"
# Serializes startups of several app instances against one database.
LOCK_KEY = 4_021_907


def migrations() -> list[tuple[str, LiteralString]]:
    # These are idempotent, version-controlled files, never user-provided SQL.
    return [
        (path.name, cast(LiteralString, path.read_text(encoding="utf-8")))
        for path in sorted(DIRECTORY.glob("*.sql"))
    ]


async def apply_migrations(conn: AsyncConnection) -> None:
    """Create any missing tables/columns. Every file only adds what is missing."""
    await conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
    try:
        for name, statement in migrations():
            # Files hold several statements, which cannot be server-side prepared.
            await conn.execute(statement, prepare=False)
            logger.info("Database migration applied", extra={"migration": name})
    finally:
        await conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def main() -> None:
    with psycopg.connect(settings.testing_db_url) as connection:
        for _, statement in migrations():
            connection.execute(statement)
    print("Greeting and WhatsApp tables are ready in the business database.")


if __name__ == "__main__":
    main()
