"""Outgoing message echoes must not reach the model or the message archive."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.application import create_app


@pytest.mark.parametrize("path", ["/invoke", "/webhook", "/webhook/shivay"])
@pytest.mark.parametrize("location", ["top", "data", "key"])
def test_outgoing_echo_no_processing(path: str, location: str) -> None:
    body = {"event": "messages.upsert", "instance": "test", "data": {}}
    target = body if location == "top" else body["data"]
    if location == "key":
        target["key"] = {}
        target = target["key"]
    target["fromMe"] = True
    with (
        patch("app.api.routes.process_message", new_callable=AsyncMock) as process,
        patch("app.whatsapp.api.get_pool") as pool,
    ):
        result = TestClient(create_app()).post(path, json=body)
        assert result.status_code == 200
        assert result.json() == (
            {"status": "ignored", "stored": 0} if path == "/webhook/shivay" else {"response": ""}
        )
        process.assert_not_awaited()
        pool.assert_not_called()


def owner_event(text: str) -> dict:
    return {
        "event": "messages.upsert",
        "instance": "test",
        "data": {
            "key": {"remoteJid": "447700900123@s.whatsapp.net", "id": "own", "fromMe": True},
            "message": {"conversation": text},
            "messageTimestamp": 1790605245,
        },
    }


@pytest.mark.parametrize(
    "text,entry_enabled,queued",
    [
        ("/add event Diwali party", True, True),
        ("/save", True, True),
        ("Hello, see you soon", True, False),
        ("/add event Diwali party", False, False),
    ],
)
def test_only_owner_commands_are_queued(text: str, entry_enabled: bool, queued: bool) -> None:
    from app.whatsapp.api import owner_command

    with patch("app.whatsapp.api.settings") as settings:
        settings.whatsapp_data_entry_enabled = entry_enabled
        assert owner_command(owner_event(text)["data"]) is queued


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "Add occassion birthday of my friend Asha, 16th october",
            "/add occassion birthday of my friend Asha, 16th october",
        ),
        ("add restaurant The Olive Tree, Richmond", "/add restaurant The Olive Tree, Richmond"),
        ("add birthday of Sam on 3 May", "/add birthday of Sam on 3 May"),
        ("/save", "/save"),
        ("Add me to the group please", None),
        ("add", None),
        ("Hello", None),
        ("[Agent]\n/add restaurant X", None),
    ],
)
def test_plain_add_commands(text: str, expected: str | None) -> None:
    from app.whatsapp.entries import command_text

    assert command_text(text) == expected


@pytest.mark.parametrize(
    "arguments,entity,supplied",
    [
        ("occassion birthday of Anjani", "occasion", "birthday of Anjani"),
        ("birthday of Sam on 3 May", "occasion", "birthday of Sam on 3 May"),
        ("restaurant: The Olive Tree", "restaurant", "The Olive Tree"),
    ],
)
def test_split_add(arguments: str, entity: str, supplied: str) -> None:
    from app.whatsapp.entries import split_add

    assert split_add(arguments) == (entity, supplied)


def test_owner_typed_values_are_normalized() -> None:
    from app.whatsapp.entries import clean_fields, validate_entry

    data = clean_fields(
        {
            "name": "Asha Rao",
            "occasion": "birthday",
            "month": 10,
            "day": 16,
            "timezone": "London",
            "phone_number": "91 9876543210",
            "country": "gb",
        }
    )
    assert data["timezone"] == "Europe/London"
    assert data["phone_number"] == "+919876543210"
    validate_entry("occasion", data)
    assert clean_fields({"timezone": "new york"})["timezone"] == "America/New_York"
    assert clean_fields({"timezone": "Asia/Kolkata"})["timezone"] == "Asia/Kolkata"
    assert clean_fields({"phone_number": "0091-98765 43210"})["phone_number"] == "+919876543210"
    # A local number has no country code; it stays invalid so the owner is asked.
    assert clean_fields({"phone_number": "07700 900123"})["phone_number"] == "07700900123"


def test_help_lists_commands_and_type_specific_fields() -> None:
    from app.whatsapp.entries import ENTITIES, EXAMPLES, help_text

    assert set(EXAMPLES) == set(ENTITIES)
    general = help_text()
    assert "/save" in general and "/help TYPE" in general and "restaurant" in general
    birthday = help_text("birthday")
    assert birthday.startswith("occasion")
    assert "Required: " in birthday and "timezone" in birthday and "country" in birthday
    assert "Optional: " in birthday and "year" in birthday
    place = help_text("place")
    assert "Required: name, location, category" in place and "add place" in place
    assert help_text("occassion").startswith("occasion")
    assert help_text("spaceship").startswith("Unknown type 'spaceship'")


@pytest.mark.asyncio
async def test_help_command_needs_no_draft_lookup() -> None:
    from unittest.mock import MagicMock

    from app.whatsapp.store import command_reply

    conn = MagicMock()
    reply = await command_reply(conn, "test", "/help place", MagicMock())
    assert reply.startswith("place")
    conn.cursor.assert_not_called()


async def test_failing_command_is_answered_and_does_not_block_queue() -> None:
    from unittest.mock import MagicMock

    from app.whatsapp.worker import WhatsAppWorker

    def context(value: object = None) -> MagicMock:
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=value)
        manager.__aexit__ = AsyncMock(return_value=False)
        return manager

    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value={"id": 5, "body": "/save"})
    lock = MagicMock()
    lock.fetchone = AsyncMock(return_value=(True,))
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=lock)
    conn.cursor.return_value = context(cur)
    conn.transaction.return_value = context()
    pool = MagicMock()
    pool.connection.return_value = context(conn)
    config = MagicMock(shivay_instance_name="test", whatsapp_owner_number="+447700900123")
    with (
        patch("app.whatsapp.worker.get_pool", return_value=pool),
        patch("app.whatsapp.worker.command_reply", new=AsyncMock(side_effect=KeyError("x"))),
        patch("app.whatsapp.worker.queue_reply", new_callable=AsyncMock) as queue,
    ):
        await WhatsAppWorker(config, MagicMock(), MagicMock()).process_command()
    assert "internal error" in queue.call_args.args[4]
    done = conn.execute.call_args_list[-1].args
    assert "command_status = 'done'" in done[0] and done[1] == (5,)
