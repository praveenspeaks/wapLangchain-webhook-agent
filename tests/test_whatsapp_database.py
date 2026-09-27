"""Opt-in PostgreSQL checks in a disposable schema, with no external messaging.

Run with WHATSAPP_TEST_DATABASE_URL pointing at a test database.
"""

import asyncio
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import LiteralString, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from psycopg import AsyncConnection, sql
from psycopg_pool import AsyncConnectionPool
from test_whatsapp import configuration, payload

from app.whatsapp.api import shivay_webhook
from app.whatsapp.llm import WhatsAppLLM
from app.whatsapp.messages import WebhookEvent
from app.whatsapp.store import command_reply, save_record
from app.whatsapp.worker import WhatsAppWorker


async def exercise_database(url: str) -> None:
    schema = "whatsapp_test_" + uuid4().hex
    root = Path(__file__).resolve().parents[1]
    admin = await AsyncConnection.connect(url, autocommit=True)
    pool = None
    try:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        # Only CREATE TABLE statements: never execute the seed script's DROP statements.
        source = (root / "schema.sql").read_text(encoding="utf-8")
        for statement in re.findall(r"CREATE TABLE .*?\n\);", source, re.S):
            await admin.execute(cast(LiteralString, statement))
        for path in sorted((root / "migrations").glob("*.sql")):
            await admin.execute(cast(LiteralString, path.read_text(encoding="utf-8")))
        pool = AsyncConnectionPool(
            url,
            open=False,
            min_size=1,
            max_size=4,
            kwargs={"autocommit": True, "options": f"-c search_path={schema}"},
        )
        await pool.open(wait=True)
        config = configuration(whatsapp_data_entry_enabled=True, whatsapp_summaries_enabled=True)
        llm, sender = WhatsAppLLM(config), AsyncMock()
        worker = WhatsAppWorker(config, sender, llm)
        with (
            patch("app.whatsapp.api.get_pool", return_value=pool),
            patch("app.whatsapp.worker.get_pool", return_value=pool),
            patch("app.whatsapp.api.settings", config),
        ):
            event = WebhookEvent.model_validate(payload())
            assert (await shivay_webhook(event, "test-secret"))["stored"] == 1
            assert (await shivay_webhook(event, "test-secret"))["stored"] == 0
            await worker.process_command()
            assert (
                await (await admin.execute("SELECT count(*) FROM whatsapp_entry_drafts")).fetchone()
            )[0] == 0

            async def command(text: str) -> str:
                async with pool.connection() as conn, conn.transaction():
                    return await command_reply(conn, "test", text, llm)

            assert "location" in await command('/add restaurant {"name":"Test Cafe"}')
            assert "Missing" in await command("/save")
            assert "Send /save" in await command('/set {"location":"London"}')
            assert "Saved restaurant" in await command("/save")
            assert "No active draft" in await command("/save")
            assert (await (await admin.execute("SELECT count(*) FROM restaurants")).fetchone())[
                0
            ] == 1

            async with pool.connection() as conn, conn.transaction():
                product = await save_record(
                    conn,
                    "product",
                    {
                        "name": "Test",
                        "price": 5,
                        "stock": 10,
                        "category": "Test",
                    },
                )
                await save_record(
                    conn, "order", {"id": "ORD-TEST", "customer_phone": "+447700900123"}
                )
                await save_record(
                    conn,
                    "order_item",
                    {
                        "order_id": "ORD-TEST",
                        "product_id": int(product),
                        "quantity": 2,
                        "unit_price": 5,
                    },
                )
            assert (await (await admin.execute("SELECT total_amount FROM orders")).fetchone())[
                0
            ] == 10
            await command(
                '/add order_item {"order_id":"ORD-TEST","product_id":9999,'
                '"quantity":1,"unit_price":1}'
            )
            assert "Draft retained" in await command("/save")
            assert "cancelled" in await command("/cancel")

            own = payload(True)
            own["data"]["key"]["id"] = "owner-command"
            own["data"]["message"] = {
                "conversation": '/add restaurant {"name":"Second","location":"York"}'
            }
            await shivay_webhook(WebhookEvent.model_validate(own), "test-secret")
            await asyncio.gather(worker.process_command(), worker.process_command())
            assert (await (await admin.execute("SELECT count(*) FROM whatsapp_outbox")).fetchone())[
                0
            ] == 1
            assert "cancelled" in await command("/cancel")
            await admin.execute(
                "UPDATE whatsapp_messages SET received_at = '2026-09-26 12:00:00+00'"
            )
            with patch.object(
                llm, "summarize", new=AsyncMock(return_value="Incoming group summary")
            ) as summarize:
                now = datetime(2026, 9, 26, 20, tzinfo=UTC)
                await asyncio.gather(worker.make_summary(now), worker.make_summary(now))
                summarize.assert_awaited_once()
                assert len(summarize.call_args.args[0]) == 1
            rows = await (await admin.execute("SELECT recipient FROM whatsapp_outbox")).fetchall()
            assert len(rows) == 2 and all(row[0] == config.whatsapp_owner_number for row in rows)
            sender.send_text.return_value = "mock-provider-id"
            await asyncio.gather(worker.send_outbox(), worker.send_outbox())
            assert sender.send_text.await_count == 2
            await worker.send_outbox()
            assert sender.send_text.await_count == 2
    finally:
        if pool is not None:
            await pool.close()
        # schema is generated above, never a configured/user-supplied schema.
        await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        await admin.close()


@pytest.mark.skipif(not os.getenv("WHATSAPP_TEST_DATABASE_URL"), reason="Opt-in PostgreSQL test")
def test_postgres_workflow() -> None:
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else asyncio.new_event_loop
    with asyncio.Runner(loop_factory=factory) as runner:
        runner.run(exercise_database(os.environ["WHATSAPP_TEST_DATABASE_URL"]))
