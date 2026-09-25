"""Open application resources at startup and close them on shutdown."""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.agent.graph import build_graph
from app.config import settings
from app.database import close_pool, init_pool
from app.greetings.repository import GreetingRepository
from app.greetings.scheduler import GreetingScheduler
from app.greetings.shivay import ShivaySender

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
    scheduler_task: asyncio.Task[None] | None = None
    sender: ShivaySender | None = None
    try:
        await checkpoint_pool.open(wait=True)
        checkpointer = AsyncPostgresSaver(checkpoint_pool)  # type: ignore[arg-type]
        await checkpointer.setup()
        app.state.runtime.graph = build_graph(checkpointer)
        await init_pool()
        if settings.greetings_enabled:
            repository = GreetingRepository()
            # Fail startup clearly if the operator has not applied the migration.
            await repository.check_schema()
            sender = ShivaySender(settings)
            scheduler = GreetingScheduler(settings, repository, sender)
            scheduler_task = asyncio.create_task(scheduler.run_forever(), name="daily-greetings")
        logger.info("Application ready")
        yield
    finally:
        app.state.runtime.graph = None
        try:
            if scheduler_task is not None:
                scheduler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await scheduler_task
        finally:
            try:
                if sender is not None:
                    await sender.aclose()
            finally:
                try:
                    await close_pool()
                finally:
                    await checkpoint_pool.close()
        logger.info("Application resources closed")
