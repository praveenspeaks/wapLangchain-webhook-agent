"""Rejected and ignored requests must be observable without logging private data."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.application import create_app


@pytest.mark.parametrize(
    "case,status,outcome",
    [
        ("invalid", 422, "invalid_payload"),
        ("ignored", 200, "ignored_from_me"),
        ("success", 200, "reply_returned"),
        ("wrong_path", 404, "route_not_found"),
        ("unauthorized", 401, "authentication_failed"),
    ],
)
def test_logs_arrival_and_outcome(case: str, status: int, outcome: str) -> None:
    client = TestClient(create_app())
    payload = {"sessionId": "diagnostic", "message": "private-body", "apikey": "private-key"}
    if case == "invalid":
        payload.pop("sessionId")
    if case == "ignored":
        payload["fromMe"] = True
    with (
        patch("app.api.request_logging.logger") as logger,
        patch("app.api.routes.process_message", new=AsyncMock(return_value="Reply")),
        patch("app.api.routes.settings") as settings,
        patch(
            "app.api.routes.shivay_webhook",
            new=AsyncMock(side_effect=HTTPException(401, "Invalid webhook secret")),
        ),
    ):
        settings.whatsapp_enabled = case == "unauthorized"
        if case == "unauthorized":
            payload.update({"event": "messages.upsert", "instance": "test", "data": {}})
        result = client.post("/wrong-route" if case == "wrong_path" else "/webhook", json=payload)
        assert result.status_code == status
        arrival, finished = logger.info.call_args_list
        assert arrival.args[0] == "Webhook request arrived"
        assert finished.kwargs["extra"]["outcome"] == outcome
        assert finished.kwargs["extra"]["status_code"] == status
        assert result.headers["X-Agent-Request-ID"] == arrival.kwargs["extra"]["request_id"]
        assert "private-body" not in str(logger.mock_calls)
        assert "private-key" not in str(logger.mock_calls)
