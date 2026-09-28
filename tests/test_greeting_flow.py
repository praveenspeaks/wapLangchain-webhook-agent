"""Explicit birthday requests cannot return invented instance setup instructions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.graph import _agent_node, _build_llm
from app.agent.greeting_flow import requests_greeting
from app.application import create_app


def test_explicit_intent_routes_to_greetings() -> None:
    assert requests_greeting(
        [
            HumanMessage(
                content="my friend has birthday on 16th october, can you add in for greeting"
            )
        ]
    )
    assert not requests_greeting([HumanMessage(content="Show birthday party tickets")])
    with patch("app.agent.graph.ChatGroq", return_value=MagicMock()) as model:
        _build_llm(force_greeting=True)
        args = model.return_value.bind_tools.call_args
        assert args.kwargs["tool_choice"] == "add_greeting_occasion"
        assert [tool.name for tool in args.args[0]] == ["add_greeting_occasion"]


@pytest.mark.asyncio
async def test_model_ignoring_required_tool_cannot_invent_instance_setup() -> None:
    model = AsyncMock()
    model.ainvoke.return_value = AIMessage(content="Let's create a default WhatsApp instance.")
    with patch("app.agent.graph._build_llm", return_value=model):
        result = await _agent_node(
            {"messages": [HumanMessage(content="add birthday greeting")]}, {}
        )
    assert "instance" not in result["messages"][0].content


def test_deployment_marker() -> None:
    assert TestClient(create_app()).get("/version").json() == {
        "service": "wapLangchain",
        "greeting_workflow": "schema-v2",
        "webhook_logging": "v1",
    }
