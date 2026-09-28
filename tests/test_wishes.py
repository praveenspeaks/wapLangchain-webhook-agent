"""Owner birthday/anniversary wishes become candidates, never records by themselves."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.whatsapp.api import owner_wish, shivay_webhook, wish_row
from app.whatsapp.messages import WebhookEvent
from app.whatsapp.store import candidate_draft, command_reply, describe_candidate
from app.whatsapp.wishes import detect_wish, phone_from_jid, recipient, region


@pytest.mark.parametrize(
    "text,occasion,name,belated",
    [
        ("Happy birthday Rahul!", "birthday", "Rahul", False),
        ("happy bday dear anjani kumar 🎂", "birthday", "Anjani Kumar", False),
        ("HBD bro", "birthday", None, False),
        ("Many happy returns of the day Priya", "birthday", "Priya", False),
        ("Happy birthday @447700900123 have a great day", "birthday", None, False),
        ("Belated happy birthday Sam, sorry!", "birthday", "Sam", True),
        ("Janamdin ki shubhkamnayein", "birthday", None, False),
        ("जन्मदिन मुबारक हो", "birthday", None, False),
        ("Happy anniversary Asha and Ravi", "anniversary", "Asha", False),
        ("Happy wedding anniversary!", "anniversary", None, False),
    ],
)
def test_detect_wish(text: str, occasion: str, name: str | None, belated: bool) -> None:
    wish = detect_wish(text)
    assert wish is not None
    assert (wish.occasion, wish.name, wish.belated) == (occasion, name, belated)


@pytest.mark.parametrize(
    "text", ["See you at the birthday party", "What a happy day", "add birthday of Sam", ""]
)
def test_ordinary_messages_are_not_wishes(text: str) -> None:
    assert detect_wish(text) is None


def item(chat: str, text: str, alt: str | None = None, context: dict | None = None) -> dict:
    message: dict[str, Any] = {"conversation": text}
    if context is not None:
        message = {"extendedTextMessage": {"text": text, "contextInfo": context}}
    key = {"remoteJid": chat, "fromMe": True, "id": "wish-1"}
    if alt:
        key["remoteJidAlt"] = alt
    return {
        "key": key,
        "message": message,
        "messageTimestamp": 1790608540,  # 2026-09-28 15:15 UTC
        "owner": "918700000000@s.whatsapp.net",
    }


def test_recipient_personal_group_mention_and_reply() -> None:
    personal = item("123@lid", "HBD", alt="919876543210@s.whatsapp.net")
    assert recipient(personal, False) == "919876543210@s.whatsapp.net"
    mention = item("1@g.us", "HBD @x", context={"mentionedJid": ["447700900123@s.whatsapp.net"]})
    assert recipient(mention, True) == "447700900123@s.whatsapp.net"
    reply = item("1@g.us", "HBD", context={"participant": "55@lid"})
    assert recipient(reply, True) == "55@lid"
    assert recipient(item("1@g.us", "HBD everyone"), True) is None
    assert phone_from_jid("919876543210@s.whatsapp.net") == "+919876543210"
    assert phone_from_jid("55@lid") is None
    assert region("+919876543210") == ("IN", "Asia/Kolkata")
    assert region("+447700900123") == ("GB", "Europe/London")
    assert region("+12025550123") is None  # Several US timezones: ask instead.


def settings_mock() -> MagicMock:
    config = MagicMock()
    config.whatsapp_birthday_capture_enabled = True
    config.whatsapp_data_entry_enabled = True
    config.whatsapp_owner_number = "+447700900999"
    config.whatsapp_summary_timezone = "Europe/London"
    config.whatsapp_enabled = True
    config.whatsapp_require_webhook_secret = False
    config.shivay_instance_name = "test"
    config.shivay_webhook_secret.get_secret_value.return_value = ""
    return config


def test_wish_row_uses_owner_local_date_and_phone() -> None:
    with patch("app.whatsapp.api.settings", settings_mock()):
        wish_item = item("123@lid", "Happy birthday Rahul", alt="919876543210@s.whatsapp.net")
        wish = owner_wish(wish_item)
        assert wish is not None
        row = wish_row(wish_item, wish)
    assert row is not None
    assert (row["month"], row["day"], row["name"]) == (9, 28, "Rahul")
    assert row["phone_number"] == "+919876543210" and not row["is_group"]


def test_capture_off_self_chat_and_commands_are_not_wishes() -> None:
    config = settings_mock()
    with patch("app.whatsapp.api.settings", config):
        own = item("1@lid", "Happy birthday me", alt="918700000000@s.whatsapp.net")
        assert owner_wish(own) is None
        assert owner_wish(item("1@lid", "/add birthday of Sam happy birthday")) is None
        config.whatsapp_birthday_capture_enabled = False
        assert owner_wish(item("1@lid", "Happy birthday Rahul")) is None


def connection(fetchone: Any = None, fetchall: Any = None) -> MagicMock:
    def context(value: object = None) -> MagicMock:
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=value)
        manager.__aexit__ = AsyncMock(return_value=False)
        return manager

    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value=fetchone)
    cur.fetchall = AsyncMock(return_value=fetchall or [])
    result = MagicMock()
    result.fetchone = AsyncMock(return_value=(1,))
    conn = MagicMock()
    conn.cursor.return_value = context(cur)
    conn.transaction.return_value = context()
    conn.execute = AsyncMock(return_value=result)
    return conn


async def test_outgoing_wish_is_stored_as_candidate_only() -> None:
    conn = connection()
    pool = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.connection.return_value.__aexit__ = AsyncMock(return_value=False)
    body = {
        "event": "messages.upsert",
        "instance": "test",
        "data": item("123@lid", "Happy birthday Rahul", alt="919876543210@s.whatsapp.net"),
    }
    with (
        patch("app.whatsapp.api.settings", settings_mock()),
        patch("app.whatsapp.api.get_pool", return_value=pool),
    ):
        result = await shivay_webhook(WebhookEvent.model_validate(body), None)
    assert result == {"status": "stored", "stored": 0, "captured": 1}
    statements = [call.args[0] for call in conn.execute.call_args_list]
    assert len(statements) == 1 and "whatsapp_occasion_candidates" in statements[0]


CANDIDATE = {
    "id": 7,
    "occasion": "birthday",
    "month": 9,
    "day": 28,
    "name": "Rahul",
    "phone_number": "+919876543210",
    "is_group": False,
    "recipient_jid": "919876543210@s.whatsapp.net",
    "belated": False,
}


async def test_candidate_becomes_complete_draft() -> None:
    data = await candidate_draft(connection(fetchone=CANDIDATE), "test", 7)
    assert data == {
        "occasion": "birthday",
        "month": 9,
        "day": 28,
        "name": "Rahul",
        "phone_number": "+919876543210",
        "country": "IN",
        "timezone": "Asia/Kolkata",
    }
    assert await candidate_draft(connection(fetchone=None), "test", 99) is None


async def test_birthday_commands() -> None:
    listing = await command_reply(
        connection(fetchall=[CANDIDATE]), "test", "/birthdays", MagicMock()
    )
    assert "#7 birthday 28 Sep · Rahul · +919876543210 · personal chat" in listing
    group = describe_candidate(CANDIDATE | {"is_group": True, "recipient_jid": None})
    assert group.endswith("group, person not identified")
    conn = connection(fetchone=None)
    reply = await command_reply(conn, "test", "add birthday 7", MagicMock())
    assert reply.startswith("Review occasion") or reply.startswith("No captured wish")
    assert (
        await command_reply(connection(), "test", "/dismiss 7", MagicMock()) == "Dismissed wish #7."
    )
