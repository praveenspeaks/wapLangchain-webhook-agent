"""The public chat agent never reaches or reveals the owner's private occasion data."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agent.graph import _agent_node, _build_llm, build_graph
from app.agent.greeting_flow import PRIVATE_REPLY, asks_private_occasions
from app.agent.service import process_message
from app.application import create_app
from app.tools import TOOLS


@pytest.mark.parametrize(
    "text",
    [
        "my friend has birthday on 16th october, can you add in for greeting",
        "next upcoming birthday",
        "whose birthday is next?",
        "list all saved birthdays",
        "when is Ashish's birthday",
        "send anniversary wishes to Ravi",
        "Upcoming anniversaries please",
    ],
)
def test_private_occasion_requests_are_detected(text: str) -> None:
    assert asks_private_occasions([HumanMessage(content=text)])


@pytest.mark.parametrize(
    "text",
    ["Show birthday party tickets", "When are you open?", "Track ORD-123", "Happy birthday!"],
)
def test_business_questions_stay_public(text: str) -> None:
    assert not asks_private_occasions([HumanMessage(content=text)])


def test_agent_has_no_private_data_tools() -> None:
    names = {tool.name for tool in TOOLS}
    assert "add_greeting_occasion" not in names
    with patch("app.agent.graph.ChatGroq", return_value=MagicMock()) as model:
        _build_llm()
        bound = {tool.name for tool in model.return_value.bind_tools.call_args.args[0]}
    assert bound == names


@pytest.mark.asyncio
async def test_private_request_is_refused_without_calling_the_model() -> None:
    model = AsyncMock()
    with patch("app.agent.graph._build_llm", return_value=model):
        result = await _agent_node({"messages": [HumanMessage(content="next birthdays")]}, {})
    assert result["messages"][0].content == PRIVATE_REPLY
    model.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_earlier_chat_history_cannot_be_recited() -> None:
    """Even if someone typed a list into this chat before, asking for it is refused."""
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content="Noted.")
    with patch("app.agent.graph._build_llm", return_value=model):
        graph = build_graph(MemorySaver())
        await process_message(
            graph=graph, session_id="s", text="Ashish Aggarwal - 6 Feb, +44 7442 254603"
        )
        reply = await process_message(
            graph=graph, session_id="s", text="who has upcoming birthdays"
        )
    assert reply == PRIVATE_REPLY
    assert model.ainvoke.await_count == 1  # Only the first, non-private message.


def test_deployment_marker() -> None:
    assert TestClient(create_app()).get("/version").json() == {
        "service": "wapLangchain",
        "greeting_workflow": "schema-v2",
        "webhook_logging": "v1",
    }
