"""Greeting chat tool must validate before writing and never create tickets."""

import json
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from psycopg.errors import UniqueViolation

from app.agent.graph import build_graph
from app.agent.greeting_flow import PRIVATE_REPLY
from app.agent.service import process_message
from app.tools.greetings import add_greeting_occasion

PARTIAL = {"name": "Anjani Kumar Singh", "occasion": "birthday", "month": 10, "day": 16}
COMPLETE = PARTIAL | {"country": "GB", "timezone": "Europe/London", "phone_number": "+447700900123"}


@pytest.mark.asyncio
async def test_missing_details_do_not_touch_database() -> None:
    with patch("app.tools.greetings.GreetingRepository") as repository:
        result = json.loads(await add_greeting_occasion.ainvoke(PARTIAL))
        assert result["status"] == "needs_details"
        assert {item["field"] for item in result["issues"]} == {
            "country",
            "timezone",
            "phone_number",
        }
        repository.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"month": 2, "day": 30},
        {"timezone": "invalid"},
        {"phone_number": "123"},
    ],
)
async def test_invalid_details_do_not_save(changes: dict) -> None:
    with patch("app.tools.greetings.GreetingRepository") as repository:
        assert (
            json.loads(await add_greeting_occasion.ainvoke(COMPLETE | changes))["status"]
            == "needs_details"
        )
        repository.assert_not_called()


@pytest.mark.asyncio
async def test_optional_year_and_duplicate_handling() -> None:
    with patch("app.tools.greetings.GreetingRepository") as repository:
        create = repository.return_value.create = AsyncMock(return_value={"id": 42})
        result = json.loads(await add_greeting_occasion.ainvoke(COMPLETE))
        assert result["status"] == "created"
        assert create.call_args.args[0].year is None
        create.side_effect = UniqueViolation()
        assert (
            json.loads(await add_greeting_occasion.ainvoke(COMPLETE))["status"] == "already_exists"
        )


@pytest.mark.asyncio
async def test_public_chat_cannot_add_occasions() -> None:
    """Adding occasions is owner-only (WhatsApp commands); the chat agent refuses."""
    model = AsyncMock()
    with (
        patch("app.agent.graph._build_llm", return_value=model),
        patch("app.tools.greetings.GreetingRepository") as repository,
    ):
        graph = build_graph(MemorySaver())
        reply = await process_message(
            graph=graph,
            session_id="birthday-test",
            text="my friend Anjani Kumar Singh has birthday on 16th October, can you add",
        )
    assert reply == PRIVATE_REPLY
    model.ainvoke.assert_not_awaited()
    repository.assert_not_called()
