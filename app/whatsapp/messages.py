"""Normalize only the webhook fields needed for storage; never retain API keys."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, StrictBool


class MessageKey(BaseModel):
    remoteJid: str = Field(min_length=1, max_length=200)
    fromMe: StrictBool
    id: str = Field(min_length=1, max_length=200)
    participant: str | None = Field(default=None, max_length=200)


class WebhookMessage(BaseModel):
    key: MessageKey
    message: dict[str, Any] = Field(default_factory=dict)
    messageTimestamp: int = Field(ge=0)
    pushName: str | None = Field(default=None, max_length=200)
    participant: str | None = Field(default=None, max_length=200)


class WebhookEvent(BaseModel):
    event: str
    instance: str = Field(min_length=1, max_length=200)
    data: dict[str, Any] | list[dict[str, Any]]


def content(message: dict[str, Any]) -> tuple[str, str]:
    """Store text and captions; record media type without downloading attachments."""
    for _ in range(4):
        wrapper = next(
            (
                message.get(k)
                for k in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2")
                if isinstance(message.get(k), dict)
            ),
            None,
        )
        if not wrapper or not isinstance(wrapper.get("message"), dict):
            break
        message = wrapper["message"]
    if isinstance(message.get("conversation"), str):
        return "text", message["conversation"]
    for key, field in (
        ("extendedTextMessage", "text"),
        ("imageMessage", "caption"),
        ("videoMessage", "caption"),
        ("documentMessage", "caption"),
        ("buttonsResponseMessage", "selectedDisplayText"),
        ("listResponseMessage", "title"),
    ):
        value = message.get(key)
        if isinstance(value, dict):
            return key, str(value.get(field) or "")
    return next(iter(message), "unknown"), ""


def normalize(data: WebhookMessage, owner_number: str) -> dict[str, Any] | None:
    jid = data.key.remoteJid
    if not jid.endswith(("@g.us", "@s.whatsapp.net", "@lid")):
        return None  # Ignore status broadcasts and channels.
    kind, text = content(data.message)
    if kind in ("protocolMessage", "reactionMessage", "senderKeyDistributionMessage"):
        return None
    sender = data.key.participant or data.participant or (None if jid.endswith("@g.us") else jid)
    owner_jid = owner_number.lstrip("+") + "@s.whatsapp.net" if owner_number else None
    from_me = data.key.fromMe or (sender == owner_jid and owner_jid is not None)
    try:
        timestamp = datetime.fromtimestamp(data.messageTimestamp, UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("Invalid messageTimestamp") from exc
    return {
        "message_id": data.key.id,
        "chat_jid": jid,
        "is_group": jid.endswith("@g.us"),
        "sender_jid": sender,
        "sender_name": data.pushName,
        "from_me": from_me,
        "message_type": kind,
        "body": text,
        "message_at": timestamp,
    }


def is_command(text: str) -> bool:
    return text.strip().split(" ", 1)[0].lower() in {
        "/add",
        "/set",
        "/save",
        "/cancel",
        "/draft",
        "/help",
    }
