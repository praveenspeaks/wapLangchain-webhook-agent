"""
tests/test_webhook.py
---------------------
Integration tests for the /api/agent endpoint.
"""

import os
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

os.environ.setdefault("GROQ_API_KEY", "test_key")
os.environ.setdefault("POSTGRES_URL", "postgresql://agent:secret@localhost:5432/test")
os.environ.setdefault("TESTING_DB_URL", "postgresql://agent:secret@localhost:5432/test")


class TestAgentEndpoint:
    def test_agent_returns_reply(self) -> None:
        with patch("server.process_message", new=AsyncMock(return_value="Hello!")):
            from server import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/agent",
                json={"sender_id": "user123", "message": "Hi"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["reply"] == "Hello!"
            assert data["status"] == "success"

    def test_agent_returns_error_on_failure(self) -> None:
        with patch("server.process_message", new=AsyncMock(side_effect=RuntimeError("boom"))):
            from server import app

            client = TestClient(app, raise_server_exceptions=False)
            resp = client.post(
                "/api/agent",
                json={"sender_id": "user123", "message": "Hi"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "error"

    def test_agent_rejects_missing_fields(self) -> None:
        from server import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/agent", json={"message": "Hi"})
        assert resp.status_code == 422  # missing sender_id


class TestHealthEndpoint:
    def test_health_returns_ok(self) -> None:
        from server import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
