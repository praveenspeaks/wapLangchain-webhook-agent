"""Debug logs redact credentials and preserve the downstream request body."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from test_whatsapp import configuration, payload

from app.application import create_app


def test_rejected_payload_diagnostics() -> None:
    config = configuration(webhook_log_payloads=True)
    body = payload() | {"apikey": "wrong-private-key", "sessionId": "debug", "message": "Hello"}
    body["data"]["message"]["messageContextInfo"] = {"messageSecret": "private-message-secret"}
    client = TestClient(create_app())
    with (
        patch("app.api.request_logging.settings", config),
        patch("app.api.payload_logging.settings", config),
        patch("app.api.routes.settings", config),
        patch("app.whatsapp.api.settings", config),
        patch("app.api.request_logging.logger") as logger,
    ):
        response = client.post(
            "/invoke", json=[{"headers": {"authorization": "private-auth"}, "body": body}]
        )
        assert response.status_code == 401
        diagnostic = logger.info.call_args_list[1].kwargs["extra"]
        assert diagnostic["payload_apikey_present"] is True
        assert diagnostic["payload_apikey_matches"] is False
        assert diagnostic["webhook_secret_header_present"] is False
        assert diagnostic["payload"][0]["body"]["message"] == "Hello"
        for secret in ("wrong-private-key", "private-message-secret", "private-auth"):
            assert secret not in str(logger.mock_calls)


def test_debug_logging_preserves_body() -> None:
    config = configuration(webhook_log_payloads=True, whatsapp_enabled=False)
    client = TestClient(create_app())
    with (
        patch("app.api.request_logging.settings", config),
        patch("app.api.payload_logging.settings", config),
        patch("app.api.routes.settings", config),
        patch(
            "app.api.routes.process_message", new=AsyncMock(return_value="Hello back")
        ) as process,
        patch("app.api.request_logging.logger"),
    ):
        response = client.post("/invoke", json={"sessionId": "debug", "message": "Hello"})
        assert response.json() == {"response": "Hello back"}
        process.assert_awaited_once()
