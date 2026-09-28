"""Authenticated, durable webhook ingestion. Slow work runs from PostgreSQL."""

import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import ValidationError

from app.config import settings
from app.database import get_pool
from app.whatsapp.messages import (
    WebhookEvent,
    WebhookMessage,
    content,
    is_command,
    is_from_me,
    message_kind,
    normalize,
)

router = APIRouter(tags=["WhatsApp"])


def owner_command(item: dict[str, Any]) -> bool:
    """Outgoing messages are echoes, except owner data-entry commands for the worker."""
    message = item.get("message")
    text = content(message)[1] if isinstance(message, dict) else ""
    return settings.whatsapp_data_entry_enabled and is_command(text)


@router.post("/webhook/shivay")
async def shivay_webhook(
    payload: WebhookEvent,
    secret: Annotated[str | None, Header(alias="X-Webhook-Secret")] = None,
) -> dict[str, int | str]:
    items = payload.data if isinstance(payload.data, list) else [payload.data]
    items = [
        item
        for item in items
        if message_kind({"messageType": payload.messageType, "data": item}) == "text"
        and (not (payload.fromMe or is_from_me(item)) or owner_command(item))
    ]
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
    rows = []
    try:
        for item in items:
            row = normalize(WebhookMessage.model_validate(item), settings.whatsapp_owner_number)
            if row is None:
                continue
            row["from_me"] = row["from_me"] or payload.fromMe or is_from_me(item)
            if row["from_me"] and not (
                settings.whatsapp_data_entry_enabled and is_command(row["body"])
            ):
                continue
            rows.append(row)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "Invalid message payload or timestamp") from exc
    stored = 0
    if not rows:
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
    return {"status": "stored", "stored": stored}


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
