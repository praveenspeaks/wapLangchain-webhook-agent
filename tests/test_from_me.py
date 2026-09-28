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
