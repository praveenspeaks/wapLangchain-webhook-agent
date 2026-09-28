"""Text-only processing with an extensible media classification boundary."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.whatsapp.messages import WebhookMessage, chat_type, message_kind, normalize


@pytest.mark.parametrize("endpoint", ["/invoke", "/webhook", "/webhook/shivay"])
@pytest.mark.parametrize(
    "kind", ["image", "audio", "video", "imageMessage", "audioMessage", "videoMessage"]
)
def test_media_skipped_even_with_flattened_caption(endpoint: str, kind: str) -> None:
    body = {
        "event": "messages.upsert",
        "instance": "test",
        "sessionId": "media",
        "messageType": kind,
        "message": "Add a birthday from this caption",
        "data": {
            "key": {"remoteJid": "person@lid", "id": "test", "fromMe": False},
            "messageTimestamp": 1790605245,
            "messageType": kind,
        },
    }
    with (
        patch("app.api.routes.process_message", new_callable=AsyncMock) as process,
        patch("app.whatsapp.api.get_pool") as pool,
    ):
        response = TestClient(create_app()).post(endpoint, json=body)
        assert response.status_code == 200
        assert response.json() == (
            {"status": "ignored", "stored": 0}
            if endpoint == "/webhook/shivay"
            else {"response": ""}
        )
        process.assert_not_awaited()
        pool.assert_not_called()


def test_wrapped_media_is_not_treated_as_flattened_text() -> None:
    assert (
        message_kind(
            {
                "messageType": "conversation",
                "message": "A caption",
                "data": {
                    "message": {
                        "ephemeralMessage": {
                            "message": {
                                "imageMessage": {"caption": "A caption"},
                            }
                        }
                    }
                },
            }
        )
        == "image"
    )
    assert (
        message_kind(
            {
                "data": {
                    "message": {
                        "extendedTextMessage": {"text": "Hello"},
                    }
                }
            }
        )
        == "text"
    )


@pytest.mark.parametrize(
    "jid,expected",
    [
        ("123@g.us", "group"),
        ("123@lid", "individual"),
        ("447700900123@s.whatsapp.net", "individual"),
        ("status@broadcast", "unknown"),
    ],
)
def test_chat_type_and_storage_classification(jid: str, expected: str) -> None:
    assert chat_type(jid) == expected
    row = normalize(
        WebhookMessage.model_validate(
            {
                "key": {"remoteJid": jid, "fromMe": False, "id": "text"},
                "message": {"conversation": "Hello"},
                "messageTimestamp": 1790605245,
            }
        ),
        "",
    )
    if expected == "unknown":
        assert row is None
    else:
        assert row is not None and row["is_group"] == (expected == "group")


def test_text_conversation_still_reaches_agent() -> None:
    with patch(
        "app.api.routes.process_message", new=AsyncMock(return_value="Hello back")
    ) as process:
        result = TestClient(create_app()).post(
            "/invoke",
            json={
                "sessionId": "individual",
                "messageType": "conversation",
                "message": "Hello",
                "remoteJid": "person@lid",
            },
        )
        assert result.json() == {"response": "Hello back"}
        process.assert_awaited_once()
