"""
tests/test_webhook.py
---------------------
Integration tests for the FastAPI webhook endpoint and Pydantic models.
"""

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("GROQ_API_KEY", "test_key")
os.environ.setdefault("SHIVAY_API_KEY", "test_shivay_key")
os.environ.setdefault("SHIVAY_INSTANCE_TOKEN", "test_token")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://agent:secret@localhost:5432/test")
os.environ.setdefault("POSTGRES_URL", "postgresql://agent:secret@localhost:5432/test")
os.environ.setdefault("WEBHOOK_SECRET", "test_webhook_secret")
os.environ.setdefault("TESTING_DB_URL", "postgresql://agent:secret@localhost:5432/test")


def _upsert_payload(
    text: str, from_me: bool = False, phone: str = "15551234567",
) -> dict[str, Any]:
    return {
        "event": "MESSAGES_UPSERT",
        "instance": "test_instance",
        "data": [
            {
                "key": {
                    "remoteJid": f"{phone}@s.whatsapp.net",
                    "fromMe": from_me,
                    "id": "TEST123",
                },
                "message": {"conversation": text},
                "messageType": "conversation",
                "messageTimestamp": 1700000000,
                "pushName": "Test User",
            }
        ],
    }


class TestWebhookEndpoint:
    def test_webhook_returns_200_for_connection_update(self) -> None:
        with patch("server.app_state") as mock_state:
            mock_state.rate_limit_hits = {}

            from server import app

            client = TestClient(app, raise_server_exceptions=False)
            payload = {
                "event": "CONNECTION_UPDATE",
                "instance": "test_instance",
                "data": {"state": "open"},
            }
            resp = client.post("/webhook/shivay", json=payload)
            assert resp.status_code == 200

    def test_webhook_ignores_outgoing_messages(self) -> None:
        with (
            patch("server._handle_message", new=AsyncMock()),
            patch("server._check_rate_limit", return_value=False),
        ):
            from server import app

            client = TestClient(app, raise_server_exceptions=False)
            payload = _upsert_payload("bot reply", from_me=True)
            resp = client.post("/webhook/shivay", json=payload)
            assert resp.status_code == 200


class TestShivayModels:
    def test_extract_text_from_conversation(self) -> None:
        from models import ShivayMessageContent

        msg = ShivayMessageContent(conversation="Hello world") # type: ignore
        assert msg.extract_text() == "Hello world"

    def test_extract_text_from_extended_text(self) -> None:
        from models import ShivayMessageContent

        msg = ShivayMessageContent(extendedTextMessage={"text": "Extended message"}) # type: ignore
        assert msg.extract_text() == "Extended message"

    def test_extract_text_from_buttons_response(self) -> None:
        from models import ShivayMessageContent

        msg = ShivayMessageContent(
            buttonsResponseMessage={"selectedDisplayText": "Yes"}
        ) # type: ignore
        assert msg.extract_text() == "Yes"

    def test_extract_text_returns_none_for_unknown(self) -> None:
        from models import ShivayMessageContent

        msg = ShivayMessageContent() # type: ignore
        assert msg.extract_text() is None
