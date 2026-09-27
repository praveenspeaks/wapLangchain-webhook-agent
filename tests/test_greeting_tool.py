"""Greeting chat tool must validate before writing and never create tickets."""

import json
import os
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from psycopg.errors import UniqueViolation

from app.agent.graph import build_graph
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
async def test_graph_collects_details_across_turns_then_saves() -> None:
    model = AsyncMock()
    model.ainvoke.side_effect = [
        AIMessage(
            content="",
            tool_calls=[{"name": "add_greeting_occasion", "args": PARTIAL, "id": "first"}],
        ),
        AIMessage(content="What are his phone number, country and timezone?"),
        AIMessage(
            content="",
            tool_calls=[{"name": "add_greeting_occasion", "args": COMPLETE, "id": "second"}],
        ),
        AIMessage(content="Saved Anjani's birthday for 16 October."),
    ]
    with (
        patch("app.agent.graph._build_llm", return_value=model),
        patch("app.tools.greetings.GreetingRepository") as repository,
        patch("app.tools.support.get_pool") as support_pool,
    ):
        create = repository.return_value.create = AsyncMock(return_value={"id": 42})
        graph = build_graph(MemorySaver())
        await process_message(
            graph=graph,
            session_id="birthday-test",
            text="my friend Anjani Kumar Singh has birthday on 16th October, can you add",
        )
        create.assert_not_awaited()
        await process_message(
            graph=graph, session_id="birthday-test", text="+447700900123, UK, London time"
        )
        create.assert_awaited_once()
        support_pool.assert_not_called()
        tool_results = [m for m in model.ainvoke.call_args.args[0] if isinstance(m, ToolMessage)]
        assert [json.loads(m.content)["status"] for m in tool_results] == [
            "needs_details",
            "created",
        ]


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv("RUN_LIVE_GROQ_TEST") != "1", reason="Opt-in live model check")
async def test_live_model_asks_only_for_missing_recipient_details() -> None:
    """Exercise the actual model decision; all database writes are forbidden."""
    with (
        patch("app.tools.greetings.GreetingRepository") as repository,
        patch(
            "app.tools.support.get_pool", side_effect=AssertionError("No support ticket")
        ) as support,
    ):
        graph = build_graph(MemorySaver())
        reply = await process_message(
            graph=graph,
            session_id="live-greeting-intent",
            text="This is a synthetic test: my fictional friend Example Person has birthday "
            "on 16th October, "
            "i want the greet him on that day",
        )
        snapshot = await graph.aget_state({"configurable": {"thread_id": "live-greeting-intent"}})
        results = [m for m in snapshot.values["messages"] if isinstance(m, ToolMessage)]
        assert results and all(m.name == "add_greeting_occasion" for m in results)
        result = json.loads(results[-1].content)
        assert result["status"] == "needs_details"
        assert {issue["field"] for issue in result["issues"]} == {
            "country",
            "timezone",
            "phone_number",
        }
        assert "instance" not in reply.lower()
        assert "api key" not in reply.lower()
        repository.assert_not_called()
        support.assert_not_called()
        print(reply)
