"""Transactional owner drafts, business inserts, and durable outgoing messages."""

from typing import Any

from psycopg import AsyncConnection, DataError, IntegrityError, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.whatsapp.entries import ENTITIES, entity_name, review, validate_entry
from app.whatsapp.llm import WhatsAppLLM


async def queue_reply(
    conn: AsyncConnection, instance: str, recipient: str, key: str, text: str
) -> None:
    # Stable part keys make webhook retries unable to enqueue the same reply twice.
    parts = [text[index : index + 3000] for index in range(0, len(text), 3000)] or [""]
    for index, part in enumerate(parts):
        await conn.execute(
            "INSERT INTO whatsapp_outbox (instance,dedup_key,recipient,body) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (instance,dedup_key) DO NOTHING",
            (instance, f"{key}:{index}", recipient, "[Agent]\n" + part),
        )


async def save_record(conn: AsyncConnection, entity: str, data: dict[str, Any]) -> str:
    validated = validate_entry(entity, data)
    values = validated.model_dump()
    table = ENTITIES[entity][0]
    if entity == "order_item":
        # Serialize total recalculation for concurrent item inserts on this order.
        cur = await conn.execute(
            "SELECT id FROM orders WHERE id = %s FOR UPDATE", (values["order_id"],)
        )
        if await cur.fetchone() is None:
            raise ValueError("order_id does not exist; create the order first")
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, values)),
        sql.SQL(",").join(sql.Placeholder() for _ in values),
    )
    cur = await conn.execute(query, tuple(values.values()))
    result = await cur.fetchone()
    assert result is not None
    if entity == "order_item":
        await conn.execute(
            "UPDATE orders SET total_amount = (SELECT COALESCE(SUM(quantity * unit_price),0) "
            "FROM order_items WHERE order_id = %s) WHERE id = %s",
            (values["order_id"], values["order_id"]),
        )
    return str(result[0])


HELP = (
    "Use /add TYPE description or /add TYPE {JSON fields}. Types: "
    + ", ".join(ENTITIES)
    + ".\nUse /set with more details or corrected JSON fields, /draft to review, "
    "/save to create the record, or /cancel to discard it. Only your outgoing commands "
    "are accepted. Feedback is private to your configured number."
)


async def command_reply(conn: AsyncConnection, instance: str, text: str, llm: WhatsAppLLM) -> str:
    command, _, arguments = text.strip().partition(" ")
    command = command.lower()
    if command == "/help":
        return HELP
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT * FROM whatsapp_entry_drafts "
            "WHERE instance = %s AND status = 'draft' FOR UPDATE",
            (instance,),
        )
        draft = await cur.fetchone()
    if command == "/add":
        if draft:
            return "You already have a draft. Use /draft, /save or /cancel before /add."
        entity, _, supplied = arguments.strip().partition(" ")
        entity = entity_name(entity)
        if entity not in ENTITIES:
            return HELP
        try:
            data = await llm.extract(entity, supplied)
        except Exception:
            return "Could not extract the record. Repeat /add TYPE with JSON; nothing was saved."
        await conn.execute(
            "INSERT INTO whatsapp_entry_drafts (instance,entity,data) VALUES (%s,%s,%s)",
            (instance, entity, Jsonb(data)),
        )
        return review(entity, data)
    if not draft:
        return "No active draft. Start with /add TYPE followed by the details."
    if command == "/cancel":
        await conn.execute(
            "UPDATE whatsapp_entry_drafts SET status = 'cancelled', "
            "updated_at = now() WHERE id = %s",
            (draft["id"],),
        )
        return "Draft cancelled. No business record was created."
    if command == "/set":
        try:
            data = draft["data"] | await llm.extract(draft["entity"], arguments)
        except Exception:
            return "Could not read the correction. Use /set {JSON fields}. The draft is unchanged."
        await conn.execute(
            "UPDATE whatsapp_entry_drafts SET data = %s, updated_at = now() WHERE id = %s",
            (Jsonb(data), draft["id"]),
        )
        return review(draft["entity"], data)
    if command == "/save":
        try:
            validate_entry(draft["entity"], draft["data"])
        except ValueError:
            return review(draft["entity"], draft["data"])
        try:
            async with conn.transaction():  # Savepoint keeps the draft on a DB constraint failure.
                record_id = await save_record(conn, draft["entity"], draft["data"])
        except ValueError as exc:
            return f"Cannot save: {exc}. Use /set to correct the draft."
        except (IntegrityError, DataError):
            return (
                "Cannot save: a referenced record is missing, an ID already exists, or a value "
                "violates a database constraint. Check IDs and values, then use /set. "
                "Draft retained."
            )
        await conn.execute(
            "UPDATE whatsapp_entry_drafts SET status = 'saved', saved_record_id = %s, "
            "updated_at = now() WHERE id = %s",
            (record_id, draft["id"]),
        )
        return f"Saved {draft['entity']} with ID {record_id}."
    return review(draft["entity"], draft["data"])
