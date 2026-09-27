"""Route explicit greeting requests and render validated tool outcomes directly."""

import json
import re

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage


def requests_greeting(messages: list[BaseMessage]) -> bool:
    last = messages[-1] if messages else None
    if not isinstance(last, HumanMessage) or not isinstance(last.content, str):
        return False
    text = last.content.lower()
    return bool(
        re.search(r"\b(birthday|anniversary)\b", text)
        and re.search(r"\b(add|greet|greeting|wish|schedule|remember|remind|send)\b", text)
    )


def greeting_reply(messages: list[BaseMessage]) -> str | None:
    last = messages[-1] if messages else None
    if not isinstance(last, ToolMessage) or last.name != "add_greeting_occasion":
        return None
    try:
        data = json.loads(str(last.content))
    except (ValueError, TypeError):
        return "I couldn't validate the greeting details. Please try again."
    if not isinstance(data, dict):
        return "I couldn't validate the greeting details. Please try again."
    if data.get("status") == "needs_details":
        labels = {
            "name": "recipient's name",
            "occasion": "birthday or anniversary",
            "month": "occasion month",
            "day": "occasion day",
            "phone_number": "recipient's WhatsApp number including + and country code",
            "country": "recipient's country",
            "timezone": "recipient's timezone or city",
            "year": "valid original year (or omit it)",
            "date": "valid occasion date",
        }
        fields = [labels.get(item["field"], "valid occasion details") for item in data["issues"]]
        return "Please provide or correct: " + "; ".join(dict.fromkeys(fields)) + "."
    if data.get("status") == "created":
        record = data["occasion"]
        result = (
            f"Saved {record['name']}'s {record['occasion']} for "
            f"{record['day']:02d}/{record['month']:02d} (record {data['id']}). "
        )
        if data["automatic_greetings_enabled"]:
            return result + f"Greetings run at {data['local_send_time']} in {record['timezone']}."
        return result + "Automatic greeting delivery is currently disabled in server settings."
    if data.get("status") == "already_exists":
        return "That phone number already has this occasion on that date. No duplicate was added."
    return "I couldn't confirm saving the greeting. Please try again later."
