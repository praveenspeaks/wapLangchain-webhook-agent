"""Transactional owner drafts, business inserts, and durable outgoing messages."""

import asyncio
import calendar
import re
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection, DataError, IntegrityError, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from app.config import settings
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
    return clean_fields(data, "occasion")


def next_date(month: int, day: int, today: date) -> date:
    """The next time this month/day comes round, today included (29 Feb -> 28 Feb)."""
    for year in (today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            candidate = date(year, month, 28)  # 29 February outside a leap year.
        if candidate >= today:
            return candidate
    raise AssertionError("A month/day always recurs within a year")


async def upcoming(conn: AsyncConnection, arguments: str, today: date) -> str:
    """Saved occasions ordered by their next date: the next 5 by default, or "all"."""
    words = re.findall(r"[a-z]+|\d+", arguments.lower())
    show_all = "all" in words
    count = next((int(word) for word in words if word.isdigit()), 5)
    kind = next(
        (
            "anniversary" if word.startswith("anniversar") else "birthday"
            for word in words
            if word.startswith(("birthday", "bday", "anniversar"))
        ),
        None,
    )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT name, occasion, month, day, phone_number FROM greeting_occasions "
            "WHERE enabled AND (%s::text IS NULL OR occasion = %s)",
            (kind, kind),
        )
        rows = list(await cur.fetchall())
    label = f"{kind or 'occasion'}s" if kind != "anniversary" else "anniversaries"
    if not rows:
        return f"No saved {label} yet. Add them with: add occasion …"
    dated = sorted(
        ((next_date(row["month"], row["day"], today), row) for row in rows),
        key=lambda pair: (pair[0], pair[1]["name"].lower()),
    )
    shown = dated if show_all else dated[: max(count, 1)]
    lines = []
    for number, (when, row) in enumerate(shown, 1):
        days = (when - today).days
        relative = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
        what = "" if kind else f" {row['occasion']}"
        lines.append(
            f"{number}. {row['name']}{what} — {when:%a} {when.day} {when:%b} ({relative})"
            f" · {row['phone_number']}"
        )
    title = f"All {len(dated)} {label}" if show_all else f"Next {len(shown)} {label}"
    reply = f"{title} (today {today:%a} {today.day} {today:%b}):\n" + "\n".join(lines)
    if len(shown) < len(dated):
        reply += f"\n\nSend upcoming all to see all {len(dated)}."
    return reply


BATCH = "batch:"  # Draft entity prefix for a list of records sent in one message.
MAX_BATCH = 25


def batch_lines(supplied: str) -> tuple[str, list[str]] | None:
    """Split a pasted list into a shared header and one record per line.

    Lines with digits (dates, phones, prices) are records; other lines, such as
    "birthday", are context applied to every record. Needs at least two records.
    """
    lines = [line.strip().lstrip("-•*·").strip() for line in supplied.splitlines()]
    lines = [line for line in lines if line]
    records = [line for line in lines if any(char.isdigit() for char in line)]
    if len(records) < 2:
        return None
    return " ".join(line for line in lines if line not in records), records


async def extract_lines(
    llm: WhatsAppLLM, entity: str, header: str, lines: list[str]
) -> list[dict[str, Any]]:
    limit = asyncio.Semaphore(4)  # Stay well inside the model's rate limit.

    async def one(line: str) -> dict[str, Any]:
        async with limit:
            try:
                return {"line": line, "data": await llm.extract(entity, f"{header} {line}".strip())}
            except Exception:
                return {"line": line, "data": None}

    return list(await asyncio.gather(*(one(line) for line in lines)))


def batch_item(entity: str, item: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """(valid values or None, one-line description or problem) for a list item."""
    if item["data"] is None:
        return None, "could not read this line"
    data = clean_fields(item["data"], entity)
    try:
        values = validate_entry(entity, data).model_dump()
    except ValidationError as exc:
        missing = sorted({str(error["loc"][0]) for error in exc.errors() if error["loc"]})
        return None, "missing or invalid: " + (", ".join(missing) or "record")
    except ValueError as exc:
        return None, str(exc)
    if entity == "occasion":
        month = calendar.month_abbr[values["month"]]
        return values, (
            f"{values['name']} · {values['occasion']} {values['day']} {month} · "
            f"{values['phone_number']} · {values['country']}, {values['timezone']}"
        )
    shown = [str(value) for value in values.values() if value not in (None, "", [])]
    return values, ", ".join(shown)[:150]


def batch_review(entity: str, items: list[dict[str, Any]]) -> str:
    lines, ready = [], 0
    for number, item in enumerate(items, 1):
        values, text = batch_item(entity, item)
        ready += values is not None
        lines.append(f"{'✓' if values else '✗'} {number}. {text if values else item['line']}")
        if values is None:
            lines.append(f"   ({text})")
    footer = f"\n\n{ready} of {len(items)} ready. Send save to create them, or cancel."
    if ready < len(items):
        footer += " Lines marked ✗ are skipped; send them again with add after fixing them."
    return f"Review {len(items)} {entity} records:\n" + "\n".join(lines) + footer


async def batch_command(conn: AsyncConnection, draft: dict[str, Any], command: str) -> str:
    entity, items = draft["entity"][len(BATCH) :], draft["data"]["items"]
    if command == "/set":
        return (
            "A list can't be corrected with set. Send save to create the ✓ records, then "
            "add the others again, or cancel and resend the whole list."
        )
    if command != "/save":
        return batch_review(entity, items)
    saved, failed = [], []
    for number, item in enumerate(items, 1):
        values, text = batch_item(entity, item)
        if values is None:
            failed.append(f"{number}. {item['line']} ({text})")
            continue
        try:
            async with conn.transaction():  # One bad record does not undo the others.
                saved.append(await save_record(conn, entity, values))
        except (ValueError, IntegrityError, DataError):
            failed.append(f"{number}. {item['line']} (already exists or violates a rule)")
    await conn.execute(
        "UPDATE whatsapp_entry_drafts SET status = 'saved', saved_record_id = %s, "
        "updated_at = now() WHERE id = %s",
        (",".join(saved)[:1000] or None, draft["id"]),
    )
    reply = f"Saved {len(saved)} of {len(items)} {entity} records"
    reply += f" (IDs {', '.join(saved)})." if saved else "."
    if failed:
        reply += "\nNot saved:\n" + "\n".join(failed)
    return reply


async def command_reply(conn: AsyncConnection, instance: str, text: str, llm: WhatsAppLLM) -> str:
    command, _, arguments = (command_text(text) or text).strip().partition(" ")
    command = command.lower()
    if command == "/help":
        return help_text(arguments)
    if command == "/birthdays":
        return await list_candidates(conn, instance)
    if command == "/upcoming":
        local = datetime.now(UTC).astimezone(ZoneInfo(settings.whatsapp_summary_timezone))
        return await upcoming(conn, arguments, local.date())
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
        cleaned = clean_fields(draft["data"], draft["entity"])
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
        batch = batch_lines(supplied)
        if entity == "occasion" and reference:
            data = await candidate_draft(conn, instance, int(reference.group(1)))
            if data is None:
                return f"No captured wish #{reference.group(1)}. Send /birthdays to list them."
        elif batch is not None:
            header, lines = batch
            if len(lines) > MAX_BATCH:
                return f"Send at most {MAX_BATCH} lines at a time; nothing was saved."
            data = {"items": await extract_lines(llm, entity, header, lines)}
            entity = BATCH + entity
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
        if entity.startswith(BATCH):
            return replaced + batch_review(entity[len(BATCH) :], data["items"])
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
    if draft["entity"].startswith(BATCH):
        return await batch_command(conn, draft, command)
    if command == "/set":
        try:
            data = clean_fields(
                draft["data"] | await llm.extract(draft["entity"], arguments), draft["entity"]
            )
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
