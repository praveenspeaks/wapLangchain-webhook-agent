"""
main.py
---------
FastAPI application — receives messages via webhook, processes them with
LangGraph + Groq, queries PostgreSQL, and returns the reply in the response.

Run locally:  uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from pythonjsonlogger.json import JsonFormatter

from agent import build_graph, process_message
from db import close_pool as close_tool_pool
from db import init_pool as init_tool_pool
from models import (
    HealthResponse,
    InvokeRequest,
    InvokeResponse,
    settings,
)

# =============================================================================
# Logging
# =============================================================================


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    formatter = JsonFormatter(
        fmt="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)


_configure_logging()
logger = logging.getLogger(__name__)

# =============================================================================
# Application state
# =============================================================================


class AppState:
    graph: Any = None
    pg_pool: AsyncConnectionPool | None = None
    start_time: float = time.monotonic()
    messages_processed: int = 0
    messages_failed: int = 0


app_state = AppState()

# =============================================================================
# Lifespan
# =============================================================================


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting agent...")

    # PostgreSQL pool (for LangGraph checkpointer)
    app_state.pg_pool = AsyncConnectionPool(
        settings.postgres_url,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    await app_state.pg_pool.open(wait=True)
    logger.info("PostgreSQL connected")

    checkpointer = AsyncPostgresSaver(app_state.pg_pool)  # type: ignore
    await checkpointer.setup()
    logger.info("Checkpointer ready")

    app_state.graph = build_graph(checkpointer)
    logger.info("Agent compiled")

    # Tool database pool (business data)
    await init_tool_pool()
    logger.info("Tool DB ready")

    logger.info("main ready")
    yield

    logger.info("Shutting down...")
    await close_tool_pool()
    if app_state.pg_pool:
        await app_state.pg_pool.close()
    logger.info("Shutdown complete")


# =============================================================================
# App
# =============================================================================

app = FastAPI(
    title="AI Agent",
    version="1.0.0",
    lifespan=lifespan,
)


# =============================================================================
# Agent Webhook
# =============================================================================


@app.post("/invoke", response_model=InvokeResponse)
async def agent_webhook(request: InvokeRequest) -> InvokeResponse:
    """
    Receives a message and returns the agent's reply in the HTTP response.
    """
    logger.info("Request received", extra={"sender_id": request.sessionId})

    try:
        response_text = await process_message(
            graph=app_state.graph,
            phone=request.sessionId,
            text=request.message,
        )

        app_state.messages_processed += 1
        return InvokeResponse(response=response_text)

    except Exception:
        app_state.messages_failed += 1
        logger.exception("Error processing request", extra={"sender_id": request.sessionId})
        return InvokeResponse(
            response="I'm sorry, I encountered an internal error. Please try again."
        )


# =============================================================================
# Health check
# =============================================================================


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="healthy")


@app.get("/metrics")
async def metrics() -> dict[str, Any]:
    return {
        "messages_processed": app_state.messages_processed,
        "messages_failed": app_state.messages_failed,
        "uptime_seconds": round(time.monotonic() - app_state.start_time, 2),
    }


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, log_config=None)
