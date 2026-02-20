"""
agent.py
--------
LangGraph agent definition.

Graph topology
--------------
  START ─► [agent] ─► (has tool calls?) ─► [tools] ──┐
                  └─► END                              │
              ◄────────────────────────────────────────┘

State: MessagesState  (list of BaseMessages)
Checkpointer: AsyncPostgresSaver  →  per-user thread = phone number
"""

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

from models import settings
from tools import TOOLS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt — optimised for WhatsApp (brief, emoji-friendly)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a friendly and efficient WhatsApp customer support assistant.

Your personality:
- Concise: WhatsApp messages should be short. Aim for 3 sentences or fewer per reply.
- Helpful: always try to resolve the customer's issue in one turn.
- Warm: use a casual, approachable tone. A single relevant emoji is fine.
- Honest: if you don't know something, say so and offer to open a ticket.

You have the following tools - USE THE RIGHT ONE FOR EACH QUERY:
- get_order_status: for ORDER ID lookups (format: ORD-XXXXX)
- get_orders_by_status: for listing orders by status (pending/paid/shipped/delivered/cancelled)
- search_product: for PRODUCT catalogue searches ONLY (physical items like headphones, chairs, etc.)
- get_product_info: for getting product details by numeric ID
- get_event_tickets: for EVENT queries ONLY (charity gala, startup pitch, music festival, conferences, workshops). ALWAYS use this for "event", "ticket", "concert", "summit", "gala", "pitch night", or "festival" questions.
- create_support_ticket: open a support ticket (needs issue description + contact)
- get_business_hours: check if we are currently open

Rules:
1. DETECT the user's intent: "event" keywords (charity, startup, gala, pitch, festival, workshop, summit) → use get_event_tickets
2. NEVER use search_product for events or tickets - that's wrong!
3. Always call a tool for orders, products, events, or hours queries.
4. Never fabricate data - tool responses are authoritative.
5. When creating a ticket, ask for contact number if not provided.
6. Keep lists to 5 items or fewer; summarise if longer.
7. Do NOT output raw JSON - humanise responses.
8. For events, always mention tickets sold and tickets remaining.
"""


# ---------------------------------------------------------------------------
# Typed state — explicit so pyright understands the messages field
# ---------------------------------------------------------------------------
class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


# ---------------------------------------------------------------------------
# LLM with tools bound  (built once per request; stateless)
# ---------------------------------------------------------------------------
def _build_llm() -> Any:
    llm = ChatGroq(
        model="llama-3.1-8b-instant",
        api_key=settings.groq_api_key, # type: ignore
        temperature=0.2,
        max_tokens=512,  # keep responses concise for WhatsApp
    )
    return llm.bind_tools(TOOLS)


# ---------------------------------------------------------------------------
# Graph nodes  (async — LangGraph 0.3 supports async nodes natively)
# ---------------------------------------------------------------------------


async def _agent_node(state: AgentState, config: RunnableConfig) -> dict[str, list[BaseMessage]]:
    """
    Decision node: async LLM call with the full conversation history.

    Prepends the system prompt so it is always present regardless of how
    many turns are already stored in the persistent thread.
    """
    llm_with_tools = _build_llm()
    messages: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]

    try:
        # Use ainvoke — non-blocking, compatible with FastAPI's event loop
        response: AIMessage = await llm_with_tools.ainvoke(messages, config)
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


# ---------------------------------------------------------------------------
# Graph builder (returns a compiled graph, NOT a checkpointer)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# High-level helper used by the webhook server
# ---------------------------------------------------------------------------


async def process_message(
    *,
    graph: Any,
    phone: str,
    text: str,
    push_name: str | None = None,
) -> str:
    """
    Run the agent for a single incoming WhatsApp message.

    Args:
        graph:     Compiled LangGraph graph (with checkpointer).
        phone:     Sender's phone number — used as the thread_id.
        text:      Message text to process.
        push_name: WhatsApp display name of the sender (optional).

    Returns:
        The agent's final text response.
    """
    from langchain_core.messages import HumanMessage

    # Enrich the user message with sender context when we have it
    user_content = text
    if push_name:
        user_content = f"[User: {push_name}] {text}"

    config: RunnableConfig = {
        "configurable": {
            "thread_id": phone,  # one persistent thread per user
        },
        "recursion_limit": 10,  # prevent infinite agent loops
    }

    logger.info(
        "Processing message",
        extra={"phone": phone, "push_name": push_name, "text_length": len(text)},
    )

    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=user_content)]},
            config=config,
        )
    except Exception as exc:
        logger.exception("Agent graph error", extra={"phone": phone, "error": str(exc)})
        return "Sorry, I encountered an error. Please try again or type *help*. 🙏"

    # Extract the last AI message content
    final_messages: list[BaseMessage] = result.get("messages", [])
    for msg in reversed(final_messages):
        if isinstance(msg, AIMessage) and msg.content:
            content = msg.content
            if isinstance(content, list):
                # multi-part content (rare with Groq, but handle gracefully)
                return " ".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            return str(content)

    return "I was unable to generate a response. Please try again. 🙏"


# ---------------------------------------------------------------------------
# Retrieve conversation history for admin endpoint
# ---------------------------------------------------------------------------


async def get_conversation_history(
    *,
    graph: Any,
    phone: str,
    limit: int = 20,
) -> list[dict[str, str]]:
    """
    Retrieve recent conversation messages for a given phone number.

    Returns a list of dicts with ``role`` and ``content`` keys.
    """
    from langchain_core.messages import AIMessage, HumanMessage

    config: RunnableConfig = {"configurable": {"thread_id": phone}}

    try:
        state = await graph.aget_state(config)
        messages: list[BaseMessage] = state.values.get("messages", [])
    except Exception as exc:
        logger.warning(
            "Could not fetch conversation state",
            extra={"phone": phone, "error": str(exc)},
        )
        return []

    history: list[dict[str, str]] = []
    for msg in messages[-limit:]:
        if isinstance(msg, HumanMessage):
            history.append({"role": "human", "content": str(msg.content)})
        elif isinstance(msg, AIMessage):
            history.append({"role": "ai", "content": str(msg.content)})

    return history
