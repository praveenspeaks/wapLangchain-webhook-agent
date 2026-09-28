"""Incoming provider API keys are ignored; secret protection is an explicit opt-in."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from test_whatsapp import configuration, payload

from app.application import create_app
from app.whatsapp.messages import WebhookEvent


@pytest.mark.parametrize(
    "key,header,expected,protected",
    [
        ("test", None, 200, False),
        (None, None, 200, False),
        ("different-key", None, 200, False),
        ("different-key", "wrong", 200, False),
        (None, "test-secret", 200, True),
        (None, None, 401, True),
        ("test", "wrong", 401, True),
        ("test", None, 401, True),
    ],
)
def test_real_hub_route_authentication(
    key: str | None, header: str | None, expected: int, protected: bool
) -> None:
    config = configuration(whatsapp_require_webhook_secret=protected)
    assert config.shivay_api_key.get_secret_value() == "test"
    body = payload()
    body.update({"sessionId": "auth-test", "message": "Hello"})
    body["data"]["message"] = {"conversation": "Hello"}
    if key is not None:
        body["apikey"] = key
    conn = AsyncMock()
    conn.transaction = MagicMock()
    conn.execute.return_value.fetchone.return_value = (1,)
    pool = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=conn)
    with (
        patch("app.api.routes.settings", config),
        patch("app.whatsapp.api.settings", config),
        patch("app.whatsapp.api.get_pool", return_value=pool),
        patch(
            "app.api.routes.process_message", new=AsyncMock(return_value="Hello back")
        ) as process,
    ):
        response = TestClient(create_app()).post(
            "/webhook",
            json=[{"body": body}],
            headers={"X-Webhook-Secret": header} if header is not None else {},
        )
        assert response.status_code == expected
        if expected == 200:
            assert response.json() == {"response": "Hello back"}
            process.assert_awaited_once()
            assert process.call_args.kwargs["text"] == "Hello"
        else:
            pool.connection.assert_not_called()
            process.assert_not_awaited()


def test_payload_secret_not_serialized_and_key_only_config_allowed() -> None:
    event = WebhookEvent.model_validate(payload() | {"apikey": "example-private-key"})
    assert "example-private-key" not in repr(event)
    assert "apikey" not in event.model_dump()
    assert configuration(
        shivay_webhook_secret="", shivay_api_key="", whatsapp_require_webhook_secret=False
    ).whatsapp_enabled
