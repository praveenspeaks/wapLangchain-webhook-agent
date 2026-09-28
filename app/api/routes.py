"""HTTP endpoints: validate input, call the agent, and return responses."""

import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.agent.service import process_message
from app.api.hub import parse_hub_request, unwrap_hub_request
from app.api.schemas import HealthResponse, InvokeResponse
from app.config import settings
from app.state import AppState
from app.whatsapp.api import shivay_webhook
from app.whatsapp.messages import WebhookEvent, event_chat_type, is_from_me, message_kind

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/invoke", response_model=InvokeResponse)
@router.post("/webhook", response_model=InvokeResponse)
async def agent_webhook(request: Request) -> InvokeResponse:
    """Wait for the agent's answer and return it in this HTTP response."""
    try:
        raw = await request.json()
    except ValueError as exc:
        raise HTTPException(422, "Expected valid JSON") from exc
    raw = unwrap_hub_request(raw)
    if is_from_me(raw):
        request.state.webhook_outcome = "ignored_from_me"
        return InvokeResponse(response="")
    kind = message_kind(raw)
    if kind != "text":
        request.state.webhook_outcome = f"ignored_{kind}"
        return InvokeResponse(response="")
    payload, ignored = parse_hub_request(raw)
    if settings.whatsapp_enabled and raw.get("event") in ("messages.upsert", "MESSAGES_UPSERT"):
        try:
            event = WebhookEvent.model_validate(raw)
        except ValidationError as exc:
            raise HTTPException(422, "Invalid WhatsApp event") from exc
        # Preserve the authenticated archive boundary, including instance checks.
        # Outgoing events have already been ignored above, including owner commands.
        stored = await shivay_webhook(event, request.headers.get("X-Webhook-Secret"))
        if stored["stored"] == 0:
            request.state.webhook_outcome = "duplicate_or_unsupported_message"
            return InvokeResponse(response="")
    if ignored:
        request.state.webhook_outcome = "outgoing_or_nontext_or_unsupported_event"
        return InvokeResponse(response="")
    state: AppState = request.app.state.runtime
    logger.info(
        "Request received",
        extra={
            "sender_id": payload.sessionId,
            "chat_type": event_chat_type(raw),
            "message_type": kind,
        },
    )
    try:
        response_text = await process_message(
            graph=state.graph,
            session_id=payload.sessionId,
            text=payload.message,
        )
        state.messages_processed += 1
        request.state.webhook_outcome = "reply_returned" if response_text else "empty_reply"
        return InvokeResponse(response=response_text)
    except Exception:
        request.state.webhook_outcome = "processing_failed"
        state.messages_failed += 1
        logger.exception("Error processing request", extra={"sender_id": payload.sessionId})
        return InvokeResponse(
            response="I'm sorry, I encountered an internal error. Please try again."
        )


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness check; this does not probe external services."""
    return HealthResponse(status="healthy")


@router.get("/version")
async def version() -> dict[str, str]:
    """Identify deployments containing deterministic greeting validation replies."""
    return {
        "service": "wapLangchain",
        "greeting_workflow": "schema-v2",
        "webhook_logging": "v1",
    }


@router.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    state: AppState = request.app.state.runtime
    return {
        "messages_processed": state.messages_processed,
        "messages_failed": state.messages_failed,
        "uptime_seconds": round(time.monotonic() - state.start_time, 2),
    }
