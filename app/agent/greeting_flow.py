"""Keep the owner's private occasion data out of the public chat agent.

Anyone can message the business, so requests about birthdays, anniversaries or
greetings get a fixed reply before any model call: the model never sees a tool
for that data, and cannot be talked into reciting it.
"""

import re

from langchain_core.messages import BaseMessage, HumanMessage

PRIVATE_REPLY = (
    "Sorry, I can't help with birthdays, anniversaries or greetings here — that "
    "information is private. I can help with orders, products, events, support "
    "tickets and opening hours. 🙏"
)
OCCASION = re.compile(r"\b(birthdays?|b'?days?|anniversar(?:y|ies)|hbd)\b")
INTENT = re.compile(
    r"\b(add|greet|greeting|greetings|wish|wishes|schedule|remember|remind|send|list"
    r"|upcoming|next|when|who|whose|saved|stored|all|coming)\b"
)
# "birthday party tickets" is an event question, which stays public.
EVENT = re.compile(r"\b(party|parties|ticket|tickets|event|events|gala|festival|concert)\b")


def asks_private_occasions(messages: list[BaseMessage]) -> bool:
    last = messages[-1] if messages else None
    if not isinstance(last, HumanMessage) or not isinstance(last.content, str):
        return False
    text = last.content.lower()
    return bool(OCCASION.search(text) and INTENT.search(text) and not EVENT.search(text))
