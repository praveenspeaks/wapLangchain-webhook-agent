"""Build the agent -> tools -> agent conversation loop."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_groq import ChatGroq
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.agent.greeting_flow import greeting_reply, requests_greeting
from app.agent.prompts import SYSTEM_PROMPT
from app.config import settings
from app.tools import TOOLS

logger = logging.getLogger(__name__)


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _build_llm(*, force_greeting: bool = False) -> Any:
    llm = ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,  # type: ignore
        temperature=0.2,
        max_tokens=512,
    )
    if force_greeting:
        return llm.bind_tools(
            [tool for tool in TOOLS if tool.name == "add_greeting_occasion"],
            tool_choice="add_greeting_occasion",
        )
    return llm.bind_tools(TOOLS)


async def _agent_node(state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
    """
    Decision node: async LLM call with the full conversation history.

    Prepends the system prompt so it is always present regardless of how
    many turns are already stored in the persistent thread.
    """
    direct_reply = greeting_reply(state["messages"])
    if direct_reply is not None:
        return {"messages": [AIMessage(content=direct_reply)]}
    force_greeting = requests_greeting(state["messages"])
    llm_with_tools = _build_llm(force_greeting=force_greeting)
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]

    try:
        # Use ainvoke — non-blocking, compatible with FastAPI's event loop
        response: AIMessage = await llm_with_tools.ainvoke(messages, config)
        if force_greeting and not response.tool_calls:
            response = AIMessage(
                content="I couldn't validate the greeting details. Please try again."
            )
    except Exception as exc:
        logger.exception("LLM call failed", extra={"error": str(exc)})
        response = AIMessage(
            content="I'm having trouble processing your request right now. "
            "Please try again in a moment. 🙏"
        )

    return {"messages": [response]}


def _should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    """
    Conditional edge: route to [tools] if the last AI message has tool calls,
    otherwise route to END.
    """
    last: BaseMessage = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "__end__"


def build_graph(checkpointer: BaseCheckpointSaver) -> Any:  # type: ignore[type-arg]
    """
    Compile and return the LangGraph StateGraph.

    Args:
        checkpointer: Any BaseCheckpointSaver (Postgres, Memory, etc.).

    Returns:
        A compiled LangGraph graph ready for ``ainvoke`` / ``astream``.
    """
    tool_node = ToolNode(TOOLS)

    # Use our explicit AgentState so the messages reducer is always applied
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("agent", _agent_node)
    graph.add_node("tools", tool_node)

    # Define edges
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", _should_continue, {"tools": "tools", "__end__": END})
    graph.add_edge("tools", "agent")  # loop: tool results go back to agent

    return graph.compile(checkpointer=checkpointer)
