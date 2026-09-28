"""Persistent command processing, private daily summaries, and outbox delivery."""

import asyncio
import logging
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row

from app.config import Settings
from app.database import get_pool
from app.greetings.shivay import DeliveryError, ShivaySender
from app.whatsapp.llm import WhatsAppLLM
from app.whatsapp.store import command_reply, queue_reply

logger = logging.getLogger(__name__)


def summary_window(now: datetime, timezone: str, cutoff: str) -> tuple[datetime, datetime] | None:
    local = now.astimezone(ZoneInfo(timezone))
    end = datetime.combine(local.date(), time.fromisoformat(cutoff), tzinfo=local.tzinfo)
    if local < end:
        return None
    # Calendar subtraction (rather than 24 UTC hours) handles 23/25-hour DST days.
    start = datetime.combine(local.date() - timedelta(days=1), end.time(), tzinfo=local.tzinfo)
    return start, end


class WhatsAppWorker:
    def __init__(self, config: Settings, sender: ShivaySender, llm: WhatsAppLLM) -> None:
        self.config, self.sender, self.llm = config, sender, llm
        self.instance = config.shivay_instance_name

    async def check_schema(self) -> None:
        async with get_pool().connection() as conn:
            for table in (
                "whatsapp_messages",
                "whatsapp_entry_drafts",
                "whatsapp_outbox",
                "whatsapp_summary_runs",
            ):
                # The identifiers are constants, not webhook input.
                from psycopg import sql

                await conn.execute(
                    sql.SQL("SELECT id FROM {} LIMIT 0").format(sql.Identifier(table))
                )

    async def process_command(self) -> None:
        async with get_pool().connection() as conn, conn.transaction():
            lock = await conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 301))", (self.instance,)
            )
            acquired = await lock.fetchone()
            if not acquired or not acquired[0]:
                return
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT id,body FROM whatsapp_messages WHERE instance = %s "
                    "AND from_me AND command_status = 'pending' ORDER BY id LIMIT 1 FOR UPDATE",
                    (self.instance,),
                )
                message = await cur.fetchone()
            if not message:
                return
            try:
                # Savepoint: a failed command rolls back its own writes but is still
                # marked done below, so it cannot block every later command.
                async with conn.transaction():
                    response = await command_reply(conn, self.instance, message["body"], self.llm)
            except Exception:
                logger.exception("WhatsApp command failed", extra={"message_id": message["id"]})
                response = (
                    "Sorry, that command failed with an internal error and nothing was saved. "
                    "Send /draft to check your draft, or /cancel to start again."
                )
            await queue_reply(
                conn,
                self.instance,
                self.config.whatsapp_owner_number,
                f"command:{message['id']}",
                response,
            )
            await conn.execute(
                "UPDATE whatsapp_messages SET command_status = 'done' WHERE id = %s",
                (message["id"],),
            )

    async def make_summary(self, now: datetime) -> None:
        window = summary_window(
            now, self.config.whatsapp_summary_timezone, self.config.whatsapp_summary_time
        )
        if window is None:
            return
        start, end = window
        owner_jid = self.config.whatsapp_owner_number.lstrip("+") + "@s.whatsapp.net"
        async with get_pool().connection() as conn, conn.transaction():
            lock = await conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 302))", (self.instance,)
            )
            acquired = await lock.fetchone()
            if not acquired or not acquired[0]:
                return
            cur = await conn.execute(
                "SELECT id FROM whatsapp_summary_runs WHERE instance = %s AND local_date = %s",
                (self.instance, end.date()),
            )
            if await cur.fetchone():
                return
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT chat_jid,sender_jid,sender_name,body,message_type,"
                    "COUNT(*) OVER() AS total "
                    "FROM whatsapp_messages WHERE instance = %s AND NOT from_me "
                    "AND sender_jid IS DISTINCT FROM %s AND received_at >= %s AND received_at < %s "
                    "ORDER BY id LIMIT %s",
                    (
                        self.instance,
                        owner_jid,
                        start,
                        end,
                        self.config.whatsapp_summary_max_messages or None,
                    ),
                )
                rows = list(await cur.fetchall())
            total = rows[0]["total"] if rows else 0
            header = (
                f"Daily incoming-message summary — {end.date()} "
                f"({self.config.whatsapp_summary_timezone})\n"
            )
            header += (
                f"Window: {start:%d %b %H:%M} to {end:%d %b %H:%M}. {total} incoming messages.\n"
            )
            summaries = []
            # Small batches stay within the configured free-tier model's context/rate budget.
            for offset in range(0, len(rows), 15):
                batch = [
                    {
                        "chat": row["chat_jid"],
                        "sender": row["sender_name"] or row["sender_jid"],
                        "text": row["body"][:600]
                        or f"[{row['message_type']}: attachment not analyzed]",
                    }
                    for row in rows[offset : offset + 15]
                ]
                summaries.append(await self.llm.summarize(batch))
                if offset + 15 < len(rows):
                    await asyncio.sleep(
                        30
                    )  # Pace multi-batch summaries; never burst free-tier calls.
            text = header + (
                "\n\n".join(summaries) if rows else "No incoming messages in this window."
            )
            if total > len(rows):
                text += f"\nPartial summary: included the first {len(rows)} of {total} messages."
            if any(len(row["body"]) > 600 for row in rows):
                text += "\nLong messages were shortened to 600 characters for summarization."
            await conn.execute(
                "INSERT INTO whatsapp_summary_runs "
                "(instance,local_date,window_start,window_end,message_count,included_count) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (self.instance, end.date(), start, end, total, len(rows)),
            )
            await queue_reply(
                conn,
                self.instance,
                self.config.whatsapp_owner_number,
                f"summary:{end.date()}",
                text,
            )

    async def send_outbox(self) -> None:
        async with get_pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "UPDATE whatsapp_outbox SET status = 'sending', attempted_at = now() WHERE id = "
                "(SELECT id FROM whatsapp_outbox WHERE instance = %s AND status = 'pending' "
                "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *",
                (self.instance,),
            )
            item = await cur.fetchone()
        if not item:
            return
        message_id = None
        try:
            message_id = await self.sender.send_text(item["recipient"], item["body"])
            status = "sent"
        except DeliveryError as exc:
            status = "unknown" if exc.uncertain else "failed"
        except Exception:
            status = "unknown"
        # A crash or cancellation leaves 'sending'; never risk a duplicate retry.
        async with get_pool().connection() as conn:
            await conn.execute(
                "UPDATE whatsapp_outbox SET status = %s, provider_message_id = %s, "
                "sent_at = CASE WHEN %s = 'sent' THEN now() ELSE NULL END WHERE id = %s",
                (status, message_id, status, item["id"]),
            )
        logger.info("WhatsApp outbox outcome", extra={"outbox_id": item["id"], "status": status})

    async def run_forever(self) -> None:
        while True:
            for task in (
                self.process_command if self.config.whatsapp_data_entry_enabled else None,
                self.send_outbox,
            ):
                if task:
                    try:
                        await task()
                    except Exception as exc:
                        logger.error(
                            "WhatsApp worker step failed",
                            extra={"step": task.__name__, "error_type": type(exc).__name__},
                            exc_info=True,
                        )
            await asyncio.sleep(5)

    async def run_summaries(self) -> None:
        while True:
            try:
                await self.make_summary(datetime.now(UTC))
            except Exception as exc:
                logger.error("WhatsApp summary failed", extra={"error_type": type(exc).__name__})
            await asyncio.sleep(60)
