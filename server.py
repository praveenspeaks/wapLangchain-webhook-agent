"""
server.py
---------
FastAPI application — receives WhatsApp messages via Shivay API webhook,
processes them with LangGraph + Groq, queries PostgreSQL, and replies.

Run locally:  uv run uvicorn server:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import BackgroundTasks, FastAPI, status
from fastapi.responses import JSONResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool
from pythonjsonlogger.json import JsonFormatter

from agent import build_graph, process_message
from db import close_pool as close_tool_pool
from db import init_pool as init_tool_pool
from shivay_client import ShivayAPIError, shivay_client
from models import (
    ShivayMessageData,
    ShivayWebhookEvent,
    HealthResponse,
    GenericAgentRequest,
    GenericAgentResponse,
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
    # In-memory rate limiting (per-phone timestamps)
    rate_limit_hits: dict[str, list[float]] = defaultdict(list)


app_state = AppState()

# =============================================================================
# Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("Starting WhatsApp agent...")

    # PostgreSQL pool (for LangGraph checkpointer)
    app_state.pg_pool = AsyncConnectionPool(
        settings.postgres_url,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    await app_state.pg_pool.open(wait=True)
    logger.info("PostgreSQL connected")

    checkpointer = AsyncPostgresSaver(app_state.pg_pool) # type: ignore
    await checkpointer.setup()
    logger.info("Checkpointer ready")

    app_state.graph = build_graph(checkpointer)
    logger.info("Agent compiled")

    # Tool database pool (business data)
    await init_tool_pool()
    logger.info("Tool DB ready")

    logger.info("Server ready")
    yield

    logger.info("Shutting down...")
    await close_tool_pool()
    if app_state.pg_pool:
        await app_state.pg_pool.close()
    await shivay_client.aclose()
    logger.info("Shutdown complete")


# =============================================================================
# App
# =============================================================================

app = FastAPI(
    title="WhatsApp AI Agent",
    version="1.0.0",
    lifespan=lifespan,
)


# =============================================================================
# In-memory rate limiting
# =============================================================================


def _check_rate_limit(phone: str) -> bool:
    """Return True if rate limit exceeded."""
    now = time.time()
    window = settings.rate_limit_window
    hits = app_state.rate_limit_hits[phone]

    # Remove old entries
    app_state.rate_limit_hits[phone] = [t for t in hits if now - t < window]
    app_state.rate_limit_hits[phone].append(now)

    return len(app_state.rate_limit_hits[phone]) > settings.rate_limit_max


# =============================================================================
# Background message handler
# =============================================================================


async def _handle_message(phone: str, text: str, push_name: str | None) -> None:
    try:
        await shivay_client.send_typing(phone)

        response_text = await process_message(
            graph=app_state.graph,
            phone=phone,
            text=text,
            push_name=push_name,
        )

        await shivay_client.send_text(phone, response_text)
        app_state.messages_processed += 1
        logger.info(
            "Message handled", extra={"phone": phone, "len": len(response_text)}
        )

    except ShivayAPIError as exc:
        app_state.messages_failed += 1
        logger.error("Failed to send reply", extra={"phone": phone, "error": str(exc)})

    except Exception as exc:
        app_state.messages_failed += 1
        logger.exception("Error handling message", extra={"phone": phone, "error": str(exc)})
        with suppress(Exception):
            await shivay_client.send_text(
                phone, "Sorry, something went wrong. Please try again."
            )


# =============================================================================
# Webhook
# =============================================================================


@app.post("/webhook/shivay", status_code=status.HTTP_200_OK)
async def webhook_shivay(
    event: ShivayWebhookEvent,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Main webhook endpoint for Shivay API."""
    return await _process_webhook_event(event, background_tasks)


@app.post("/webhook/evolution", status_code=status.HTTP_200_OK)
async def webhook_evolution(
    event: ShivayWebhookEvent,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    """Legacy webhook endpoint (backward compatibility)."""
    logger.info("Webhook received (legacy /evolution endpoint)", extra={"event": event.event, "instance": event.instance})
    return await _process_webhook_event(event, background_tasks)


async def _process_webhook_event(
    event: ShivayWebhookEvent,
    background_tasks: BackgroundTasks,
) -> JSONResponse:
    logger.info("Webhook received", extra={"event": event.event, "instance": event.instance})

    event_name = event.event.upper().replace(".", "_")
    if event_name == "MESSAGES_UPSERT":
        raw_messages: list[dict[str, Any]] = (
            event.data if isinstance(event.data, list) else [event.data]  # type: ignore[list-item]
        )

        for raw in raw_messages:
            try:
                msg = ShivayMessageData.model_validate(raw)
            except Exception as exc:
                logger.warning("Could not parse message", extra={"error": str(exc)})
                continue

            if msg.key.from_me:
                continue

            text = msg.message.extract_text() if msg.message else None
            if not text:
                continue

            # Keep full JID for LID-based numbers
            remote_jid = msg.key.remote_jid
            if remote_jid.endswith("@s.whatsapp.net"):
                phone = remote_jid.split("@")[0]
            else:
                phone = remote_jid

            if _check_rate_limit(phone):
                await shivay_client.send_text(
                    phone, f"Too many messages. Please wait {settings.rate_limit_window}s."
                )
                continue

            background_tasks.add_task(_handle_message, phone, text, msg.push_name)

    return JSONResponse(content={"status": "ok"})


# =============================================================================
# Simplified Generic Webhook
# =============================================================================


@app.post("/api/agent", response_model=GenericAgentResponse)
async def simple_agent_webhook(request: GenericAgentRequest) -> GenericAgentResponse:
    """
    Simplified endpoint for external systems.
    Receives input and returns the agent's reply directly in the HTTP response.
    """
    logger.info("Generic request received", extra={"sender_id": request.sender_id})

    try:
        # Use existing logic to process the message
        # We use sender_id as the thread_id for conversation memory
        response_text = await process_message(
            graph=app_state.graph,
            phone=request.sender_id,
            text=request.message,
        )

        app_state.messages_processed += 1
        return GenericAgentResponse(reply=response_text)

    except Exception as exc:
        app_state.messages_failed += 1
        logger.exception("Error in simplified agent endpoint", extra={"sender_id": request.sender_id})
        return GenericAgentResponse(
            reply="I'm sorry, I encountered an internal error. Please try again.",
            status="error"
        )


# =============================================================================
# Health check
# =============================================================================


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", environment=settings.environment)


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

    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True, log_config=None)
