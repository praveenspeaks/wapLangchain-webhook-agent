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
