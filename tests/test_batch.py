"""A pasted list becomes one reviewable draft, and save creates every valid line."""

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from app.whatsapp.entries import command_text
from app.whatsapp.store import batch_lines, batch_review, command_reply, extract_lines

LIST = """add occasion birthday

Ashish Aggarwal - 6 Feb, phone +44 7442 254603, london
Manish Sahal - 15 Aug, phone +91 63983 96518, india
Bhavesh Jhaveri - 21 Sep, phone +44 7818 282666, London
Rohit Kaila - 26 May, Phone +44 7944 620333, London
Rohit Malik - 22 June, phone +44 7920 538402, London
Manish Gupta - 12 July, phone +44 7459 819114, London
Abhinav Pathak - 9 August, phone +44 7776 843183, London"""

MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}


class FakeLLM:
    """Returns what the model returns for one line: raw values, cleaned afterwards."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def extract(self, entity: str, text: str) -> dict[str, Any]:
        from app.whatsapp.entries import clean_fields

        self.calls.append(text)
        name, rest = text.removeprefix("birthday ").split(" - ", 1)
        day, month = re.match(r"(\d+) (\w+)", rest).groups()  # type: ignore[union-attr]
        phone = re.search(r"\+[\d ]+\d", rest).group(0)  # type: ignore[union-attr]
        place = rest.rsplit(",", 1)[1].strip()
        raw = {
            "name": name,
            "occasion": "birthday",
            "day": int(day),
            "month": MONTHS[month[:3].lower()],
            "phone_number": phone,
            "timezone": place,
        }
        return clean_fields(raw, entity)


def test_list_is_recognised_as_a_command_with_seven_records() -> None:
    command = command_text(LIST, self_chat=True)
    assert command is not None and command.startswith("/add occasion birthday")
    split = batch_lines(command.split(" ", 2)[2])
    assert split is not None
    header, lines = split
    assert header == "birthday" and len(lines) == 7


async def test_every_line_is_extracted_with_the_header_and_cleaned() -> None:
    llm = FakeLLM()
    lines = batch_lines(LIST.split("\n", 1)[1])[1]  # type: ignore[index]
    items = await extract_lines(llm, "occasion", "birthday", lines)  # type: ignore[arg-type]
    assert all(call.startswith("birthday ") for call in llm.calls)
    review = batch_review("occasion", items)
    assert review.startswith("Review 7 occasion records:")
    assert "✓ 1. Ashish Aggarwal · birthday 6 Feb · +447442254603 · GB, Europe/London" in review
    assert "✓ 2. Manish Sahal · birthday 15 Aug · +916398396518 · IN, Asia/Kolkata" in review
    assert "7 of 7 ready. Send save to create them, or cancel." in review


def connection(draft: dict | None) -> MagicMock:
    def context(value: object = None) -> MagicMock:
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=value)
        manager.__aexit__ = AsyncMock(return_value=False)
        return manager

    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchone = AsyncMock(return_value=draft)
    ids = iter(range(100, 200))
    result = MagicMock()
    result.fetchone = AsyncMock(side_effect=lambda: (next(ids),))
    conn = MagicMock()
    conn.cursor.return_value = context(cur)
    conn.transaction.return_value = context()
    conn.execute = AsyncMock(return_value=result)
    return conn


async def test_add_list_then_save_creates_all_valid_records() -> None:
    conn = connection(None)
    reply = await command_reply(conn, "test", command_text(LIST, True) or "", FakeLLM())  # type: ignore[arg-type]
    assert "7 of 7 ready" in reply
    insert = conn.execute.call_args_list[-1].args
    assert insert[1][1] == "batch:occasion"
    draft = {"id": 1, "entity": "batch:occasion", "data": insert[1][2].obj}
    conn = connection(draft)
    saved = await command_reply(conn, "test", "/save", MagicMock())
    assert saved.startswith("Saved 7 of 7 occasion records (IDs 100, 101")
    inserts = [
        c.args for c in conn.execute.call_args_list if "greeting_occasions" in repr(c.args[0])
    ]
    assert len(inserts) == 7


async def test_unreadable_and_incomplete_lines_are_reported_not_saved() -> None:
    items = [
        {"line": "Sam - 3 May, +44 7700 900123, London", "data": None},
        {
            "line": "Ann - 4 May",
            "data": {"name": "Ann", "occasion": "birthday", "day": 4, "month": 5},
        },
    ]
    review = batch_review("occasion", items)
    assert "✗ 1. Sam - 3 May" in review and "could not read this line" in review
    assert "✗ 2. Ann - 4 May" in review and "phone_number" in review
    assert "0 of 2 ready" in review
    draft = {"id": 1, "entity": "batch:occasion", "data": {"items": items}}
    saved = await command_reply(connection(draft), "test", "/save", MagicMock())
    assert saved.startswith("Saved 0 of 2 occasion records.")
    assert "Not saved:" in saved
