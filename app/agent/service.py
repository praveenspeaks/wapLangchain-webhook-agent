"""Run a chat turn and extract the final user-facing answer."""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)


async def process_message(
    *,
    graph: Any,
    session_id: str,
    text: str,
) -> str:
    """
    Run the agent for a single incoming message.

    Args:
        graph: Compiled LangGraph graph (with checkpointer).
        session_id: Session ID — used as the thread_id for conversation memory.
        text:  Message text to process.

    Returns:
        The agent's final text response.
    """
    from langchain_core.messages import HumanMessage

    config: RunnableConfig = {
        "configurable": {
            "thread_id": session_id,
        },
        "recursion_limit": 10,
    }

    logger.info(
        "Processing message",
        extra={"sender_id": session_id, "text_length": len(text)},
    )

    try:
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content=text)]},
            config=config,
        )
    except Exception as exc:
        logger.exception("Agent graph error", extra={"sender_id": session_id, "error": str(exc)})
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
