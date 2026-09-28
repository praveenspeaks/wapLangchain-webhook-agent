"""Authenticated, durable webhook ingestion. Slow work runs from PostgreSQL."""

import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import ValidationError

from app.config import settings
from app.database import get_pool
from app.whatsapp.messages import WebhookEvent, WebhookMessage, is_from_me, message_kind, normalize

router = APIRouter(tags=["WhatsApp"])


@router.post("/webhook/shivay")
async def shivay_webhook(
    payload: WebhookEvent,
    secret: Annotated[str | None, Header(alias="X-Webhook-Secret")] = None,
) -> dict[str, int | str]:
    items = payload.data if isinstance(payload.data, list) else [payload.data]
    if payload.fromMe or (items and all(is_from_me(item) for item in items)):
        return {"status": "ignored", "stored": 0}
    items = [
        item
        for item in items
        if message_kind(
            {
                "messageType": payload.messageType,
                "data": item,
            }
        )
        == "text"
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
    try:
        rows = [
            normalize(WebhookMessage.model_validate(item), settings.whatsapp_owner_number)
            for item in items
            if not is_from_me(item)
        ]
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "Invalid message payload or timestamp") from exc
    stored = 0
    rows = [row for row in rows if row is not None and not row["from_me"]]
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
                    "ignored",
                ),
            )
            stored += int(await cur.fetchone() is not None)
    return {"status": "stored", "stored": stored}
