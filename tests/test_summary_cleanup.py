"""Summarized WhatsApp data is deleted so the database does not keep growing."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from app.whatsapp.worker import WhatsAppWorker


async def test_prune_deletes_summarized_messages_but_keeps_pending_work() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    config = MagicMock(shivay_instance_name="test")
    end = datetime(2026, 9, 28, 20, tzinfo=UTC)
    await WhatsAppWorker(config, MagicMock(), MagicMock()).prune(conn, end)
    statements = [(call.args[0], call.args[1]) for call in conn.execute.call_args_list]
    messages, outbox, drafts = statements
    assert "DELETE FROM whatsapp_messages" in messages[0]
    assert "received_at < LEAST(%s, now() - interval '10 minutes')" in messages[0]
    assert "command_status <> 'pending'" in messages[0]
    assert messages[1] == ("test", end)
    assert "status = 'sent'" in outbox[0] and "7 days" in outbox[0]
    assert "('saved', 'cancelled')" in drafts[0]
    # Captured birthday wishes and greeting records are owner data: never pruned.
    assert not any("candidates" in sql or "greeting" in sql for sql, _ in statements)
