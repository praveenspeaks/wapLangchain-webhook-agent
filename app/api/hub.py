"""Extract chat input from Connection Hub events without trusting delivery metadata."""

from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.schemas import InvokeRequest
from app.whatsapp.messages import content


def unwrap_hub_request(raw: Any) -> dict[str, Any]:
    """Unwrap one n8n item, without silently discarding batched messages."""
    if isinstance(raw, list):
        if len(raw) != 1:
            raise HTTPException(422, "Send exactly one message per webhook request")
        raw = raw[0]
    if not isinstance(raw, dict):
        raise HTTPException(422, "Expected a message object")
    if "body" in raw:
        raw = raw["body"]
    if not isinstance(raw, dict):
        raise HTTPException(422, "Expected an object in body")
    return raw


def parse_hub_request(raw: Any) -> tuple[InvokeRequest, bool]:
    """Return sanitized chat input and whether to ignore this event.

    API keys, URLs, headers and provider metadata never reach the language model.
    """
    raw = unwrap_hub_request(raw)
    data = raw.get("data", {})
    data = data if isinstance(data, dict) else {}
    key = data.get("key", {})
    key = key if isinstance(key, dict) else {}
    flags = [v for v in (key.get("fromMe"), data.get("fromMe"), raw.get("fromMe")) if v is not None]
    if any(type(flag) is not bool for flag in flags):
        raise HTTPException(422, "fromMe must be a boolean")
    event = raw.get("event")
    ignored = any(flags) or (
        event is not None and event not in ("messages.upsert", "MESSAGES_UPSERT")
    )
    text = raw.get("message")
    if not isinstance(text, str):
        message = data.get("message", text)
        text = content(message)[1] if isinstance(message, dict) else None
    session = raw.get("sessionId")
    if not session:
        instance = raw.get("instanceId") or raw.get("instance")
        chat = key.get("remoteJid") or raw.get("remoteJid")
        if isinstance(instance, str) and isinstance(chat, str):
            session = f"whatsapp-{instance}-{chat}"
    # Empty/non-text provider events have no text response to send.
    if event is not None and not text:
        ignored = True
    try:
        request = InvokeRequest.model_validate(
            {
                "sessionId": session,
                "message": text if isinstance(text, str) else "",
            }
        )
    except ValidationError as exc:
        # Do not return raw provider payloads (which may contain API keys) in errors.
        raise HTTPException(422, "A nonempty sessionId and text message are required") from exc
    if not request.sessionId.strip() or (not ignored and not request.message.strip()):
        raise HTTPException(422, "A nonempty sessionId and text message are required")
    return request, ignored
