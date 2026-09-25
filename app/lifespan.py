"""Open application resources at startup and close them on shutdown."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.agent.graph import build_graph
from app.config import settings
from app.database import close_pool, init_pool

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Use separate pools for conversation checkpoints and business queries."""
    logger.info("Configured Groq model", extra={"model": settings.groq_model})
    checkpoint_pool = AsyncConnectionPool(
        settings.postgres_url,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    try:
        await checkpoint_pool.open(wait=True)
        checkpointer = AsyncPostgresSaver(checkpoint_pool)  # type: ignore[arg-type]
        await checkpointer.setup()
        app.state.runtime.graph = build_graph(checkpointer)
        await init_pool()
        logger.info("Application ready")
        yield
    finally:
        app.state.runtime.graph = None
        try:
            await close_pool()
        finally:
            await checkpoint_pool.close()
        logger.info("Application resources closed")
