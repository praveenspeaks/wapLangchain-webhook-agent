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


async def test_hourly_retention_prunes_older_than_configured_days() -> None:
    import asyncio
    from datetime import timedelta
    from unittest.mock import patch

    import pytest

    from app.whatsapp.worker import run_retention

    conn = MagicMock()
    pool = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    config = MagicMock(shivay_instance_name="test", whatsapp_archive_retention_days=2)
    with (
        patch("app.whatsapp.worker.get_pool", return_value=pool),
        patch("app.whatsapp.worker.prune", new_callable=AsyncMock) as prune,
        patch("app.whatsapp.worker.asyncio.sleep", side_effect=asyncio.CancelledError),
        pytest.raises(asyncio.CancelledError),
    ):
        await run_retention(config)
    instance, before = prune.call_args.args[1:]
    assert instance == "test"
    age = datetime.now(UTC) - before
    assert timedelta(days=2) <= age < timedelta(days=2, minutes=1)


def test_retention_of_one_day_is_rejected_with_summaries() -> None:
    import pytest
    from test_whatsapp import configuration

    with pytest.raises(ValueError, match="at least 2"):
        configuration(whatsapp_summaries_enabled=True, whatsapp_archive_retention_days=1)
    configuration(whatsapp_summaries_enabled=True, whatsapp_archive_retention_days=0)
