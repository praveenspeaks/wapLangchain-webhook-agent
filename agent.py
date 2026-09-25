"""Compatibility imports. New code should use app.agent."""

from typing import Any

from app.agent.graph import build_graph as build_graph
from app.agent.service import process_message as _process_message


async def process_message(*, graph: Any, phone: str, text: str) -> str:
    """Support the original phone argument for existing Python callers."""
    return await _process_message(graph=graph, session_id=phone, text=text)
