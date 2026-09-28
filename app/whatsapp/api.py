"""Authenticated, durable webhook ingestion. Slow work runs from PostgreSQL."""

import secrets
from datetime import UTC, datetime
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException
from pydantic import ValidationError

from app.config import settings
from app.database import get_pool
from app.whatsapp.entries import command_text
from app.whatsapp.messages import (
    WebhookEvent,
    WebhookMessage,
    chat_type,
    content,
    is_from_me,
    message_kind,
    normalize,
)
from app.whatsapp.wishes import Wish, detect_wish, phone_from_jid, recipient

router = APIRouter(tags=["WhatsApp"])


def is_self_chat(item: dict[str, Any]) -> bool:
    """The owner's "message yourself" chat: the chat JID is the account's own number."""
    key = item.get("key")
    key = key if isinstance(key, dict) else {}
    own = {item.get("owner")}
    if settings.whatsapp_owner_number:
        own.add(settings.whatsapp_owner_number.lstrip("+") + "@s.whatsapp.net")
    own.discard(None)
    return bool(own & {key.get("remoteJid"), key.get("remoteJidAlt")})


def item_text(item: dict[str, Any]) -> str:
    message = item.get("message")
    return content(message)[1] if isinstance(message, dict) else ""


def owner_command(item: dict[str, Any], text: str | None = None) -> str | None:
    """Outgoing messages are echoes, except owner data-entry commands for the worker."""
    if not settings.whatsapp_data_entry_enabled:
        return None
    return command_text(item_text(item) if text is None else text, is_self_chat(item))


def owner_wish(item: dict[str, Any]) -> Wish | None:
    """A birthday/anniversary wish the owner sent to someone (text or photo caption)."""
    if not settings.whatsapp_birthday_capture_enabled or is_self_chat(item):
        return None
    text = item_text(item)
    return None if command_text(text) else detect_wish(text)


def wish_row(item: dict[str, Any], wish: Wish) -> dict[str, Any] | None:
    data = WebhookMessage.model_validate(item)
    kind = chat_type(data.key.remoteJid)
    if kind == "unknown":
        return None  # Status broadcasts and channels.
    person = recipient(item, kind == "group")
    try:
        sent = datetime.fromtimestamp(data.messageTimestamp, UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise ValueError("Invalid messageTimestamp") from exc
    # The owner wished on their own calendar day.
    local = sent.astimezone(ZoneInfo(settings.whatsapp_summary_timezone))
    return {
        "message_id": data.key.id,
        "chat_jid": data.key.remoteJid,
        "is_group": kind == "group",
        "recipient_jid": person,
        "phone_number": phone_from_jid(person),
        "name": wish.name,
        "occasion": wish.occasion,
        "month": local.month,
        "day": local.day,
        "belated": wish.belated,
        "body": item_text(item)[:1000],
        "message_at": sent,
    }


@router.post("/webhook/shivay")
async def shivay_webhook(
    payload: WebhookEvent,
    secret: Annotated[str | None, Header(alias="X-Webhook-Secret")] = None,
) -> dict[str, int | str]:
    items = payload.data if isinstance(payload.data, list) else [payload.data]

    def wanted(item: dict[str, Any]) -> bool:
        text = message_kind({"messageType": payload.messageType, "data": item}) == "text"
        if not (payload.fromMe or is_from_me(item)):
            return text
        return (text and owner_command(item) is not None) or owner_wish(item) is not None

    items = [item for item in items if wanted(item)]
    if not items:
        return {"status": "ignored", "stored": 0}
    expected = settings.shivay_webhook_secret.get_secret_value()
    if not settings.whatsapp_enabled:
        raise HTTPException(503, "WhatsApp capture is disabled")
    # Provider API credentials are for outbound calls, never incoming authentication.
    if settings.whatsapp_require_webhook_secret and (
        not secret or not expected or not secrets.compare_digest(secret.encode(), expected.encode())
    ):
        raise HTTPException(401, "Invalid webhook secret")
    if payload.instance != settings.shivay_instance_name:
        raise HTTPException(403, "Unexpected WhatsApp instance")
    if payload.event.upper().replace(".", "_") != "MESSAGES_UPSERT":
        return {"status": "ignored", "stored": 0}
    if len(items) > 100:
        raise HTTPException(413, "Maximum 100 messages per webhook")
    rows, wishes = [], []
    try:
        for item in items:
            from_me = payload.fromMe or is_from_me(item)
            wish = owner_wish(item) if from_me else None
            if wish is not None:
                candidate = wish_row(item, wish)
                if candidate is not None:
                    wishes.append(candidate)
                continue
            row = normalize(WebhookMessage.model_validate(item), settings.whatsapp_owner_number)
            if row is None:
                continue
            row["from_me"] = row["from_me"] or from_me
            if row["from_me"]:
                command = owner_command(item, row["body"])
                if command is None:
                    continue
                row["body"] = command  # The worker receives the canonical command.
            rows.append(row)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "Invalid message payload or timestamp") from exc
    stored = captured = 0
    if not rows and not wishes:
        return {"status": "ignored", "stored": 0}
    async with get_pool().connection() as conn, conn.transaction():
        for row in rows:
            cur = await conn.execute(
                "INSERT INTO whatsapp_messages "
                "(instance, message_id, chat_jid, is_group, sender_jid, sender_name, from_me, "
                "message_type, body, message_at, command_status) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (instance,chat_jid,message_id) DO NOTHING RETURNING id",
                (
                    payload.instance,
                    row["message_id"],
                    row["chat_jid"],
                    row["is_group"],
                    row["sender_jid"],
                    row["sender_name"],
                    row["from_me"],
                    row["message_type"],
                    row["body"],
                    row["message_at"],
                    # Owner commands wait for the worker, which replies privately.
                    "pending" if row["from_me"] else "ignored",
                ),
            )
            stored += int(await cur.fetchone() is not None)
        for wish in wishes:
            cur = await conn.execute(
                "INSERT INTO whatsapp_occasion_candidates "
                "(instance, message_id, chat_jid, is_group, recipient_jid, phone_number, name, "
                "occasion, month, day, belated, body, message_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (instance,chat_jid,message_id) DO NOTHING RETURNING id",
                (
                    payload.instance,
                    wish["message_id"],
                    wish["chat_jid"],
                    wish["is_group"],
                    wish["recipient_jid"],
                    wish["phone_number"],
                    wish["name"],
                    wish["occasion"],
                    wish["month"],
                    wish["day"],
                    wish["belated"],
                    wish["body"],
                    wish["message_at"],
                ),
            )
            captured += int(await cur.fetchone() is not None)
    return {"status": "stored", "stored": stored, "captured": captured}


async def claim_reply(event: WebhookEvent) -> bool:
    """Atomically claim the single agent reply for an archived incoming message.

    Hubs can deliver one message several times (a native event plus an agent
    request, or retries); only the first claim answers. 'done' marks it answered.
    """
    key = event.data.get("key") if isinstance(event.data, dict) else None
    if not isinstance(key, dict):
        return False
    async with get_pool().connection() as conn:
        cur = await conn.execute(
            "UPDATE whatsapp_messages SET command_status = 'done' WHERE instance = %s "
            "AND chat_jid = %s AND message_id = %s AND NOT from_me "
            "AND command_status = 'ignored' RETURNING id",
            (event.instance, key.get("remoteJid"), key.get("id")),
        )
        return await cur.fetchone() is not None
