"""Exercise the graph and session memory without Groq or PostgreSQL."""

from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agent.graph import build_graph
from app.agent.service import process_message


@pytest.mark.asyncio
async def test_graph_runs_tool_and_remembers_only_matching_session() -> None:
    model = AsyncMock()
    model.ainvoke.side_effect = [
        AIMessage(
            content="",
            tool_calls=[{"name": "get_business_hours", "args": {}, "id": "hours-1"}],
        ),
        AIMessage(content="Here are our hours."),
        AIMessage(content="We were discussing opening hours."),
        AIMessage(content="Hello, new visitor."),
    ]
    with patch("app.agent.graph._build_llm", return_value=model):
        graph = build_graph(MemorySaver())
        assert (
            await process_message(graph=graph, session_id="first", text="When are you open?")
            == "Here are our hours."
        )
        assert (
            await process_message(graph=graph, session_id="first", text="What were we discussing?")
            == "We were discussing opening hours."
        )
        await process_message(graph=graph, session_id="second", text="Hello")

    after_tool = model.ainvoke.call_args_list[1].args[0]
    assert any(isinstance(message, ToolMessage) for message in after_tool)
    follow_up = model.ainvoke.call_args_list[2].args[0]
    assert [m.content for m in follow_up if isinstance(m, HumanMessage)] == [
        "When are you open?",
        "What were we discussing?",
    ]
    new_session = model.ainvoke.call_args_list[3].args[0]
    assert [m.content for m in new_session if isinstance(m, HumanMessage)] == ["Hello"]


@pytest.mark.asyncio
async def test_legacy_agent_call_still_accepts_phone() -> None:
    from agent import process_message as legacy_process_message

    graph = AsyncMock()
    graph.ainvoke.return_value = {"messages": [AIMessage(content="Hello")]}
    assert await legacy_process_message(graph=graph, phone="old-client", text="Hi") == "Hello"
    assert graph.ainvoke.call_args.kwargs["config"]["configurable"]["thread_id"] == "old-client"
