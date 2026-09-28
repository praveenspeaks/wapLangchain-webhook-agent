"""Transactional owner drafts, business inserts, and durable outgoing messages."""

import calendar
import re
from typing import Any

from psycopg import AsyncConnection, DataError, IntegrityError, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.whatsapp.entries import (
    ENTITIES,
    clean_fields,
    command_text,
    help_text,
    review,
    split_add,
    validate_entry,
)
from app.whatsapp.llm import WhatsAppLLM
from app.whatsapp.wishes import region


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


# "add birthday 12" / "add occasion #12" refers to captured wish 12.
CANDIDATE_REF = re.compile(r"(?:(?:birthday|anniversary)\s+)?#?(\d{1,12})", re.IGNORECASE)


def describe_candidate(row: dict[str, Any]) -> str:
    parts = [f"#{row['id']} {row['occasion']} {row['day']} {calendar.month_abbr[row['month']]}"]
    parts.append(row["name"] or "name unknown")
    parts.append(row["phone_number"] or "no number")
    if row["is_group"]:
        parts.append("group" if row["recipient_jid"] else "group, person not identified")
    else:
        parts.append("personal chat")
    if row["belated"]:
        parts.append("belated wish: the date may be earlier")
    return " · ".join(parts)


async def list_candidates(conn: AsyncConnection, instance: str) -> str:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT * FROM whatsapp_occasion_candidates WHERE instance = %s AND status = 'new' "
            "ORDER BY id DESC LIMIT 20",
            (instance,),
        )
        rows = list(await cur.fetchall())
    if not rows:
        return "No captured wishes yet. Birthday and anniversary wishes you send appear here."
    return (
        "Captured wishes (newest first):\n"
        + "\n".join(describe_candidate(row) for row in rows)
        + "\n\nSend: add birthday N to review one, or dismiss N to remove it from this list."
    )


async def dismiss_candidate(conn: AsyncConnection, instance: str, arguments: str) -> str:
    reference = CANDIDATE_REF.fullmatch(arguments.strip())
    if not reference:
        return "Send dismiss N, where N is a number from /birthdays."
    cur = await conn.execute(
        "UPDATE whatsapp_occasion_candidates SET status = 'dismissed' "
        "WHERE instance = %s AND id = %s AND status = 'new' RETURNING id",
        (instance, int(reference.group(1))),
    )
    if await cur.fetchone() is None:
        return f"No captured wish #{reference.group(1)}. Send /birthdays to list them."
    return f"Dismissed wish #{reference.group(1)}."


async def candidate_draft(
    conn: AsyncConnection, instance: str, candidate_id: int
) -> dict[str, Any] | None:
    """Draft fields from a captured wish; the owner still reviews and saves it."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "UPDATE whatsapp_occasion_candidates SET status = 'added' "
            "WHERE instance = %s AND id = %s AND status = 'new' RETURNING *",
            (instance, candidate_id),
        )
        row = await cur.fetchone()
    if row is None:
        return None
    data: dict[str, Any] = {"occasion": row["occasion"], "month": row["month"], "day": row["day"]}
    if row["name"]:
        data["name"] = row["name"]
    if row["phone_number"]:
        data["phone_number"] = row["phone_number"]
        if located := region(row["phone_number"]):
            data["country"], data["timezone"] = located
    return clean_fields(data)


async def command_reply(conn: AsyncConnection, instance: str, text: str, llm: WhatsAppLLM) -> str:
    command, _, arguments = (command_text(text) or text).strip().partition(" ")
    command = command.lower()
    if command == "/help":
        return help_text(arguments)
    if command == "/birthdays":
        return await list_candidates(conn, instance)
    if command == "/dismiss":
        return await dismiss_candidate(conn, instance, arguments)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT * FROM whatsapp_entry_drafts "
            "WHERE instance = %s AND status = 'draft' FOR UPDATE",
            (instance,),
        )
        draft = await cur.fetchone()
    if draft and command != "/add":
        # Drafts saved before a cleanup rule existed are fixed when next used.
        cleaned = clean_fields(draft["data"])
        if cleaned != draft["data"]:
            await conn.execute(
                "UPDATE whatsapp_entry_drafts SET data = %s, updated_at = now() WHERE id = %s",
                (Jsonb(cleaned), draft["id"]),
            )
            draft["data"] = cleaned
    if command == "/add":
        entity, supplied = split_add(arguments)
        if entity not in ENTITIES:
            return help_text(entity or " ")
        reference = CANDIDATE_REF.fullmatch(supplied.strip())
        if entity == "occasion" and reference:
            data = await candidate_draft(conn, instance, int(reference.group(1)))
            if data is None:
                return f"No captured wish #{reference.group(1)}. Send /birthdays to list them."
        else:
            try:
                data = await llm.extract(entity, supplied)
            except Exception:
                return (
                    "Could not extract the record. Repeat /add TYPE with JSON; nothing was saved."
                )
        replaced = ""
        if draft:
            # A new add replaces the unsaved draft instead of being refused.
            await conn.execute(
                "UPDATE whatsapp_entry_drafts SET status = 'cancelled', "
                "updated_at = now() WHERE id = %s",
                (draft["id"],),
            )
            replaced = f"(Your previous unsaved {draft['entity']} draft was discarded.)\n"
        await conn.execute(
            "INSERT INTO whatsapp_entry_drafts (instance,entity,data) VALUES (%s,%s,%s)",
            (instance, entity, Jsonb(data)),
        )
        return replaced + review(entity, data)
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
            data = clean_fields(draft["data"] | await llm.extract(draft["entity"], arguments))
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
