"""Connection Hub payloads are normalized; credentials never enter chat input."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.application import create_app


def hub_body() -> dict:
    return {
        "event": "messages.upsert",
        "instance": "test-instance",
        "instanceId": "instance-1",
        "sessionId": "whatsapp-instance-1-group@g.us",
        "message": "List electricians",
        "apikey": "sensitive-do-not-forward",
        "server_url": "https://unused.example",
        "data": {
            "key": {"remoteJid": "group@g.us", "id": "message-1", "fromMe": False},
            "message": {"conversation": "List electricians"},
        },
    }


@pytest.mark.parametrize("shape", ["direct", "wrapped", "n8n"])
@pytest.mark.parametrize("path", ["/invoke", "/webhook"])
def test_hub_payload_returns_only_reply(shape: str, path: str) -> None:
    body = hub_body()
    payload = body if shape == "direct" else {"headers": {}, "body": body}
    if shape == "n8n":
        payload = [payload]
    app = create_app()
    app.state.runtime.graph = object()
    with patch(
        "app.api.routes.process_message", new=AsyncMock(return_value="Here is the list")
    ) as process:
        response = TestClient(app).post(path, json=payload)
        assert response.status_code == 200
        assert response.json() == {"response": "Here is the list"}
        process.assert_awaited_once_with(
            graph=app.state.runtime.graph, session_id=body["sessionId"], text="List electricians"
        )


def test_native_event_is_archived_without_reply() -> None:
    """Native provider events are notifications; the hub's agent request replies."""
    body = hub_body()
    del body["message"], body["sessionId"]
    with (
        patch("app.api.routes.settings") as settings,
        patch(
            "app.api.routes.shivay_webhook", new=AsyncMock(return_value={"stored": 1})
        ) as archive,
        patch("app.api.routes.claim_reply", new_callable=AsyncMock) as claim,
        patch("app.api.routes.process_message", new_callable=AsyncMock) as process,
    ):
        settings.whatsapp_enabled = True
        assert TestClient(create_app()).post("/webhook", json=body).json() == {"response": ""}
        archive.assert_awaited_once()
        claim.assert_not_awaited()
        process.assert_not_awaited()


def test_agent_request_replies_after_native_copy_was_archived() -> None:
    """The observed double delivery: the native copy is stored first, so the agent
    request's insert is a duplicate, yet it must still carry the one reply."""
    with (
        patch("app.api.routes.settings") as settings,
        patch("app.api.routes.shivay_webhook", new=AsyncMock(return_value={"stored": 0})),
        patch("app.api.routes.claim_reply", new=AsyncMock(return_value=True)),
        patch("app.api.routes.process_message", new=AsyncMock(return_value="Hi!")) as process,
    ):
        settings.whatsapp_enabled = True
        result = TestClient(create_app()).post("/webhook", json=hub_body())
        assert result.json() == {"response": "Hi!"}
        process.assert_awaited_once()


@pytest.mark.parametrize("kind", ["outgoing", "other_event", "media"])
def test_ignored_events_do_not_create_reply_loops(kind: str) -> None:
    body = hub_body()
    if kind == "outgoing":
        body["data"]["key"]["fromMe"] = True
    elif kind == "other_event":
        body["event"] = "connection.update"
    else:
        del body["message"]
        body["data"]["message"] = {"audioMessage": {"url": "https://unused.example"}}
    with patch("app.api.routes.process_message", new_callable=AsyncMock) as process:
        assert TestClient(create_app()).post("/webhook", json=body).json() == {"response": ""}
        process.assert_not_awaited()


def test_invalid_payload_errors_do_not_expose_secrets_or_drop_batches() -> None:
    client = TestClient(create_app())
    body = hub_body() | {"sessionId": {"apikey": "secret"}}
    response = client.post("/webhook", json=body)
    assert response.status_code == 422
    assert "secret" not in response.text
    assert client.post("/webhook", json=[hub_body(), hub_body()]).status_code == 422
    assert client.post("/webhook", json={"message": "Hi"}).status_code == 422
    assert client.post("/webhook", content="not JSON").status_code == 422


@pytest.mark.parametrize("stored", [1, 0])
def test_from_me_commands_are_queued_without_chat_reply(stored: int) -> None:
    body = hub_body()
    body["data"]["key"]["fromMe"] = True
    body["message"] = "/add restaurant Test"
    body["data"]["message"] = {"conversation": "/add restaurant Test"}
    with (
        patch("app.api.routes.settings") as settings,
        patch(
            "app.api.routes.shivay_webhook", new=AsyncMock(return_value={"stored": stored})
        ) as archive,
        patch("app.api.routes.process_message", new_callable=AsyncMock) as process,
    ):
        settings.whatsapp_enabled = True
        result = TestClient(create_app()).post(
            "/webhook", json=[{"body": body}], headers={"X-Webhook-Secret": "test-secret"}
        )
        assert result.json() == {"response": ""}
        archive.assert_awaited_once()
        process.assert_not_awaited()


def test_duplicate_reply_claim_does_not_run_agent_again() -> None:
    with (
        patch("app.api.routes.settings") as settings,
        patch("app.api.routes.shivay_webhook", new=AsyncMock(return_value={"stored": 0})),
        patch("app.api.routes.claim_reply", new=AsyncMock(return_value=False)),
        patch("app.api.routes.process_message", new_callable=AsyncMock) as process,
    ):
        settings.whatsapp_enabled = True
        result = TestClient(create_app()).post("/webhook", json=hub_body())
        assert result.json() == {"response": ""}
        process.assert_not_awaited()
