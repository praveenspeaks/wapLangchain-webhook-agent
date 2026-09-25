"""HTTP endpoints: validate input, call the agent, and return responses."""

import logging
import time
from typing import Any

from fastapi import APIRouter, Request

from app.agent.service import process_message
from app.api.schemas import HealthResponse, InvokeRequest, InvokeResponse
from app.state import AppState

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/invoke", response_model=InvokeResponse)
async def agent_webhook(payload: InvokeRequest, request: Request) -> InvokeResponse:
    """Wait for the agent's answer and return it in this HTTP response."""
    state: AppState = request.app.state.runtime
    logger.info("Request received", extra={"sender_id": payload.sessionId})
    try:
        response_text = await process_message(
            graph=state.graph,
            session_id=payload.sessionId,
            text=payload.message,
        )
        state.messages_processed += 1
        return InvokeResponse(response=response_text)
    except Exception:
        state.messages_failed += 1
        logger.exception("Error processing request", extra={"sender_id": payload.sessionId})
        return InvokeResponse(
            response="I'm sorry, I encountered an internal error. Please try again."
        )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness check; this does not probe external services."""
    return HealthResponse(status="healthy")


@router.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    state: AppState = request.app.state.runtime
    return {
        "messages_processed": state.messages_processed,
        "messages_failed": state.messages_failed,
        "uptime_seconds": round(time.monotonic() - state.start_time, 2),
    }
