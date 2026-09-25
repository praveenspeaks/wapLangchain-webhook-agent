"""Manage the business-data connection pool used by tools."""

from __future__ import annotations

import logging

from psycopg_pool import AsyncConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None


async def init_pool() -> None:
    """Create and open the tool-database connection pool."""
    global _pool  # noqa: PLW0603
    _pool = AsyncConnectionPool(
        settings.testing_db_url,
        min_size=2,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    await _pool.open(wait=True)
    logger.info("Tool DB pool opened")


async def close_pool() -> None:
    """Close the tool-database connection pool."""
    global _pool  # noqa: PLW0603
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("Tool DB pool closed")


def get_pool() -> AsyncConnectionPool:
    """Return the live pool. Raises RuntimeError if not initialised."""
    if _pool is None:
        msg = "Tool DB pool not initialised. Call init_pool() first."
        raise RuntimeError(msg)
    return _pool
