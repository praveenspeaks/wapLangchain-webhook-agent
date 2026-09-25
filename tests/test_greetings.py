"""Calendar, sender, scheduler, and management API tests without real messaging."""

import asyncio
import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.application import create_app
from app.config import Settings
from app.greetings.scheduler import GreetingScheduler, due_date
from app.greetings.schemas import RecipientTimezone
from app.greetings.shivay import DeliveryError, ShivaySender


def config(**updates: object) -> Settings:
    return Settings.model_validate(
        {
            "groq_api_key": "test",
            "postgres_url": "postgresql://localhost/test",
            "greetings_enabled": False,
            "greetings_time": "09:00",
            "shivay_api_url": "https://shivay.example/api",
            "shivay_api_key": "test-key",
            "shivay_instance_name": "test instance",
            **updates,
        }
    )


@pytest.mark.parametrize("value", ["25:00", "9:00", "09:00:00", "09:70"])
def test_invalid_schedule_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        config(greetings_time=value)


def test_missing_provider_configuration_rejected_when_enabled() -> None:
    with pytest.raises(ValidationError):
        config(greetings_enabled=True, shivay_api_key="")


def test_schedule_uses_local_date_and_same_day_catchup() -> None:
    now = datetime(2026, 9, 25, 3, 29, tzinfo=UTC)
    assert due_date(now, "Asia/Kolkata", "09:00") is None
    assert str(due_date(now.replace(minute=30), "Asia/Kolkata", "09:00")) == "2026-09-25"
    assert str(due_date(now.replace(hour=20), "Asia/Kolkata", "00:30")) == "2026-09-26"


def test_dst_clock_change_uses_wall_time() -> None:
    # At the repeated 01:30 in London, both instants have the same occurrence date.
    first = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    second = first.replace(hour=1)
    assert due_date(first, "Europe/London", "01:30") == due_date(second, "Europe/London", "01:30")


@pytest.mark.asyncio
async def test_shivay_request_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.raw_path == b"/api/message/sendText/test%20instance"
        assert request.headers["apikey"] == "test-key"
        assert json.loads(request.content) == {"number": "447700900123", "text": "Happy birthday!"}
        return httpx.Response(201, json={"key": {"id": "provider-123"}})

    sender = ShivaySender(config(), transport=httpx.MockTransport(handler))
    try:
        assert await sender.send_text("+447700900123", "Happy birthday!") == "provider-123"
    finally:
        await sender.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,uncertain", [(401, False), (429, False), (503, True), (408, True)])
async def test_provider_errors_are_not_retried(status: int, uncertain: bool) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status)

    sender = ShivaySender(config(), transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(DeliveryError) as caught:
            await sender.send_text("+447700900123", "Hello")
        assert caught.value.uncertain is uncertain
        assert len(calls) == 1
    finally:
        await sender.aclose()


@pytest.mark.asyncio
async def test_unrecognized_success_response_is_uncertain() -> None:
    sender = ShivaySender(
        config(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"unexpected": "response"})
        ),
    )
    try:
        with pytest.raises(DeliveryError) as caught:
            await sender.send_text("+447700900123", "Hello")
        assert caught.value.uncertain
    finally:
        await sender.aclose()


def repository() -> AsyncMock:
    repo = AsyncMock()
    repo.timezones.return_value = ["UTC"]
    repo.due.return_value = [
        {"id": 1, "name": "Alex", "occasion": "birthday", "phone_number": "+447700900123"}
    ]
    repo.claim.return_value = 11
    return repo


@pytest.mark.asyncio
async def test_repeated_checks_send_only_claimed_occurrences() -> None:
    repo, sender = repository(), AsyncMock()
    repo.claim.side_effect = [11, None]
    sender.send_text.return_value = "message-1"
    scheduler = GreetingScheduler(
        config(), repo, sender, lambda: datetime(2026, 9, 25, 9, tzinfo=UTC)
    )
    await scheduler.run_once()
    await scheduler.run_once()
    sender.send_text.assert_awaited_once()
    repo.finish.assert_awaited_once_with(11, "sent", "message-1")


@pytest.mark.asyncio
async def test_before_schedule_does_not_query_recipients() -> None:
    repo = repository()
    await GreetingScheduler(
        config(), repo, AsyncMock(), lambda: datetime(2026, 9, 25, 8, tzinfo=UTC)
    ).run_once()
    repo.due.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_delivery_is_recorded_without_retry() -> None:
    repo, sender = repository(), AsyncMock()
    sender.send_text.side_effect = DeliveryError(uncertain=True, code="transport_error")
    await GreetingScheduler(
        config(), repo, sender, lambda: datetime(2026, 9, 25, 9, tzinfo=UTC)
    ).run_once()
    repo.finish.assert_awaited_once_with(11, "unknown", None)
    sender.send_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_send_keeps_claim_for_manual_review() -> None:
    repo, sender = repository(), AsyncMock()
    sender.send_text.side_effect = asyncio.CancelledError()
    scheduler = GreetingScheduler(
        config(), repo, sender, lambda: datetime(2026, 9, 25, 9, tzinfo=UTC)
    )
    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_once()
    repo.finish.assert_not_awaited()


def test_management_api_is_protected() -> None:
    with patch("app.greetings.api.settings") as settings:
        settings.greetings_admin_api_key = SecretStr("admin-test")
        client = TestClient(create_app())
        assert client.get("/greetings/occasions").status_code == 401
        assert (
            client.get("/greetings/deliveries", headers={"X-Greetings-Key": "wrong"}).status_code
            == 401
        )
        with patch("app.greetings.api.repository.list_occasions", new=AsyncMock(return_value=[])):
            assert (
                client.get("/greetings/occasions", headers={"X-Greetings-Key": "admin-test"}).json()
                == []
            )


def test_management_api_disabled_without_key() -> None:
    with patch("app.greetings.api.settings") as settings:
        settings.greetings_admin_api_key = SecretStr("")
        assert TestClient(create_app()).get("/greetings/occasions").status_code == 503


@pytest.mark.parametrize("payload", [{}, {"timezone": "Not/AZone"}, {"timezone": ""}])
def test_recipient_timezone_is_required_and_valid(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        RecipientTimezone.model_validate(payload)


@pytest.mark.asyncio
async def test_each_timezone_waits_for_its_own_nine_am() -> None:
    repo, sender = repository(), AsyncMock()
    repo.timezones.return_value = ["Asia/Kolkata", "America/New_York"]
    repo.due.return_value = []
    await GreetingScheduler(
        config(), repo, sender, lambda: datetime(2026, 9, 25, 8, tzinfo=UTC)
    ).run_once()
    repo.due.assert_awaited_once_with(date(2026, 9, 25), "Asia/Kolkata")
    repo.due.reset_mock()
    await GreetingScheduler(
        config(), repo, sender, lambda: datetime(2026, 9, 25, 13, tzinfo=UTC)
    ).run_once()
    assert repo.due.await_args_list[-1].args == (date(2026, 9, 25), "America/New_York")


@pytest.mark.asyncio
async def test_each_timezone_uses_its_local_occurrence_year() -> None:
    repo = repository()
    repo.timezones.return_value = ["Pacific/Kiritimati", "Pacific/Honolulu"]
    await GreetingScheduler(
        config(), repo, AsyncMock(), lambda: datetime(2026, 12, 31, 20, tzinfo=UTC)
    ).run_once()
    assert repo.claim.await_args_list[0].args == (1, date(2027, 1, 1), "Pacific/Kiritimati")
    assert repo.claim.await_args_list[1].args == (1, date(2026, 12, 31), "Pacific/Honolulu")


@pytest.mark.asyncio
async def test_invalid_database_timezone_does_not_block_other_recipients() -> None:
    repo = repository()
    repo.timezones.return_value = ["Invalid/Timezone", "UTC"]
    await GreetingScheduler(
        config(), repo, AsyncMock(), lambda: datetime(2026, 9, 25, 9, tzinfo=UTC)
    ).run_once()
    repo.due.assert_awaited_once_with(date(2026, 9, 25), "UTC")


def test_timezone_update_validates_before_writing() -> None:
    with patch("app.greetings.api.settings") as settings:
        settings.greetings_admin_api_key = SecretStr("admin-test")
        with patch(
            "app.greetings.api.repository.set_timezone", new=AsyncMock(return_value=True)
        ) as update:
            client = TestClient(create_app())
            headers = {"X-Greetings-Key": "admin-test"}
            assert (
                client.patch(
                    "/greetings/occasions/1/timezone", json={"timezone": "bad"}, headers=headers
                ).status_code
                == 422
            )
            update.assert_not_awaited()
            assert (
                client.patch(
                    "/greetings/occasions/1/timezone",
                    json={"timezone": "Asia/Kolkata"},
                    headers=headers,
                ).status_code
                == 200
            )
            update.assert_awaited_once_with(1, "Asia/Kolkata")
