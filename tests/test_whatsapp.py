"""Offline checks for webhook boundaries, validation, and London scheduling."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.application import create_app
from app.config import Settings
from app.whatsapp.entries import review, validate_entry
from app.whatsapp.llm import WhatsAppLLM
from app.whatsapp.messages import WebhookMessage, is_command, normalize
from app.whatsapp.worker import summary_window


def configuration(**changes: object) -> Settings:
    return Settings.model_validate(
        {
            "groq_api_key": "test",
            "postgres_url": "postgresql://localhost/test",
            "whatsapp_enabled": True,
            "shivay_webhook_secret": "test-secret",
            "shivay_instance_name": "test",
            "whatsapp_owner_number": "+447700900123",
            "shivay_api_url": "https://example.com",
            "shivay_api_key": "test",
            **changes,
        }
    )


def payload(from_me: bool = False, chat: str = "123@g.us") -> dict:
    return {
        "event": "messages.upsert",
        "instance": "test",
        "data": {
            "key": {
                "remoteJid": chat,
                "fromMe": from_me,
                "id": "unique",
                "participant": "447700900999@s.whatsapp.net",
            },
            "messageTimestamp": 1790351821,
            "message": {"conversation": "/add restaurant X"},
        },
    }


@pytest.mark.parametrize("day,hour,elapsed", [(29, 20, 23), (25, 21, 25)])
def test_london_daylight_saving(day: int, hour: int, elapsed: int) -> None:
    month = 3 if elapsed == 23 else 10
    now = datetime(2026, month, day, hour, tzinfo=UTC)
    window = summary_window(now, "Europe/London", "21:00")
    assert window is not None
    start, end = window
    assert (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() == elapsed * 3600
    assert summary_window(now.replace(hour=hour - 1), "Europe/London", "21:00") is None


def test_incoming_group_and_owner_flags() -> None:
    incoming = normalize(WebhookMessage.model_validate(payload()["data"]), "+447700900123")
    assert incoming and incoming["is_group"] and not incoming["from_me"]
    outgoing = normalize(WebhookMessage.model_validate(payload(True)["data"]), "+447700900123")
    assert outgoing and outgoing["from_me"]
    assert is_command(outgoing["body"])
    assert not is_command("[Agent]\n/add restaurant X")
    assert (
        normalize(WebhookMessage.model_validate(payload(chat="status@broadcast")["data"]), "")
        is None
    )


def test_required_fields_and_database_constraints() -> None:
    assert "location" in review("restaurant", {"name": "Cafe"})
    assert "Send /save" in review("restaurant", {"name": "Cafe", "location": "London"})
    with pytest.raises(ValidationError):
        validate_entry("product", {"name": "X", "price": -1, "stock": 1, "category": "X"})
    with pytest.raises(ValidationError):
        validate_entry(
            "order_item", {"order_id": "ORD-1", "product_id": 1, "quantity": 0, "unit_price": 1}
        )
    with pytest.raises(ValueError):
        validate_entry("restaurant", {"name": "X", "location": "London", "sql": "DROP"})


@pytest.mark.parametrize(
    "changes",
    [
        {"whatsapp_summaries_enabled": True, "whatsapp_owner_number": ""},
        {"whatsapp_enabled": False, "whatsapp_data_entry_enabled": True},
        {"whatsapp_summary_timezone": "London"},
        {"whatsapp_summary_time": "25:00"},
        {"shivay_webhook_secret": ""},
    ],
)
def test_invalid_settings_fail_at_startup(changes: dict) -> None:
    with pytest.raises(ValidationError):
        configuration(**changes)


def test_webhook_rejects_unauthenticated_and_wrong_instance() -> None:
    with (
        patch("app.whatsapp.api.settings", configuration()),
        patch("app.whatsapp.api.get_pool") as pool,
    ):
        client = TestClient(create_app())
        assert client.post("/webhook/shivay", json=payload()).status_code == 401
        bad = payload() | {"instance": "someone-else"}
        assert (
            client.post(
                "/webhook/shivay", json=bad, headers={"X-Webhook-Secret": "test-secret"}
            ).status_code
            == 403
        )
        pool.assert_not_called()


@pytest.mark.asyncio
async def test_json_entry_needs_no_llm_and_unknown_fields_rejected() -> None:
    llm = WhatsAppLLM(configuration())
    with patch.object(llm, "complete", new_callable=AsyncMock) as complete:
        assert await llm.extract("restaurant", '{"name":"Cafe"}') == {"name": "Cafe"}
        with pytest.raises(ValueError):
            await llm.extract("restaurant", '{"arbitrary_table":"X"}')
        complete.assert_not_awaited()
