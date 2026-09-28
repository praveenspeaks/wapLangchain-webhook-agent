"""Normalize only the webhook fields needed for storage; never retain API keys."""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, SecretStr, StrictBool


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
    fromMe: StrictBool = False
    messageType: str | None = None
    instance: str = Field(min_length=1, max_length=200)
    data: dict[str, Any] | list[dict[str, Any]]
    apikey: SecretStr | None = Field(default=None, exclude=True, repr=False)


def is_from_me(value: dict[str, Any]) -> bool:
    """Recognize true flags in provider bodies, data objects and message keys."""
    if value.get("fromMe") is True:
        return True
    key = value.get("key")
    if isinstance(key, dict) and key.get("fromMe") is True:
        return True
    data = value.get("data")
    if isinstance(data, dict):
        return data.get("fromMe") is True or (
            isinstance(data.get("key"), dict) and data["key"].get("fromMe") is True
        )
    return False


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
    if message_kind({"message": data.message}) != "text":
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
        "is_group": chat_type(jid) == "group",
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


def chat_type(jid: str) -> str:
    """LID and phone-number JIDs identify individuals; group IDs end in @g.us."""
    if jid.endswith("@g.us"):
        return "group"
    if jid.endswith(("@lid", "@s.whatsapp.net")):
        return "individual"
    return "unknown"


def message_kind(event: dict[str, Any]) -> str:
    """Dispatch point for future media transcription. Captions are not conversations.

    Inspect both declared type and nested content so a hub's flattened caption
    cannot accidentally turn an attachment into a text command.
    """
    data = event.get("data")
    data = data if isinstance(data, dict) else {}
    aliases = {
        "conversation": "text",
        "text": "text",
        "extendedTextMessage": "text",
        "image": "image",
        "imageMessage": "image",
        "audio": "audio",
        "audioMessage": "audio",
        "ptt": "audio",
        "video": "video",
        "videoMessage": "video",
    }
    kinds = []
    for value in (event.get("messageType"), data.get("messageType")):
        if isinstance(value, str) and value:
            kinds.append(aliases.get(value, "unsupported"))
    for value in (event.get("message"), data.get("message")):
        if isinstance(value, dict):
            kind, _ = content(value)
            kinds.append(aliases.get(kind, "unsupported"))
    for kind in kinds:
        if kind != "text":
            return kind
    # Legacy /invoke accepts a plain text message without provider metadata.
    return "text" if kinds or isinstance(event.get("message"), str) else "unsupported"


def event_chat_type(event: dict[str, Any]) -> str:
    data = event.get("data")
    data = data if isinstance(data, dict) else {}
    key = data.get("key")
    key = key if isinstance(key, dict) else {}
    jid = key.get("remoteJid") or data.get("remoteJid") or event.get("remoteJid")
    return chat_type(jid) if isinstance(jid, str) else "unknown"
