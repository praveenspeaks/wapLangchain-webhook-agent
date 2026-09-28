"""Owner-only "upcoming" lists saved occasions by their next date, not table order."""

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.whatsapp.entries import command_text
from app.whatsapp.store import next_date, upcoming

TODAY = date(2026, 9, 28)
PEOPLE = [
    ("Ashish Aggarwal", 2, 6),
    ("Manish Sahal", 8, 15),
    ("Bhavesh Jhaveri", 9, 21),
    ("Rohit Kaila", 5, 26),
    ("Rohit Malik", 6, 22),
    ("Manish Gupta", 7, 12),
    ("Abhinav Pathak", 8, 9),
    ("Asha Rao", 9, 28),
    ("Sam Lee", 9, 29),
]


def rows(kind: str | None = None) -> list[dict[str, Any]]:
    everyone = [
        {"name": n, "occasion": "birthday", "month": m, "day": d, "phone_number": "+44"}
        for n, m, d in PEOPLE
    ] + [
        {"name": "Ravi & Neha", "occasion": "anniversary", "month": 10, "day": 3,
         "phone_number": "+91"},
    ]  # fmt: skip
    return [row for row in everyone if kind is None or row["occasion"] == kind]


def conn_for(kind: str | None = None) -> MagicMock:
    cur = MagicMock()
    cur.execute = AsyncMock()
    cur.fetchall = AsyncMock(return_value=rows(kind))
    manager = MagicMock()
    manager.__aenter__ = AsyncMock(return_value=cur)
    manager.__aexit__ = AsyncMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = manager
    return conn


def test_next_date_wraps_to_next_year_and_handles_leap_day() -> None:
    assert next_date(9, 28, TODAY) == date(2026, 9, 28)  # today counts
    assert next_date(9, 21, TODAY) == date(2027, 9, 21)  # just passed
    assert next_date(2, 29, TODAY) == date(2027, 2, 28)


async def test_next_five_birthdays_in_date_order() -> None:
    reply = await upcoming(conn_for("birthday"), "birthday", TODAY)
    lines = reply.splitlines()
    assert lines[0] == "Next 5 birthdays (today Mon 28 Sep):"
    assert lines[1] == "1. Asha Rao — Mon 28 Sep (today) · +44"
    assert lines[2] == "2. Sam Lee — Tue 29 Sep (tomorrow) · +44"
    assert lines[3].startswith("3. Ashish Aggarwal — Sat 6 Feb (in 131 days)")
    assert [line.split(" — ")[0] for line in lines[4:6]] == ["4. Rohit Kaila", "5. Rohit Malik"]
    assert reply.endswith("Send upcoming all to see all 9.")


async def test_count_all_and_mixed_occasions() -> None:
    three = await upcoming(conn_for(), "3", TODAY)
    assert three.startswith("Next 3 occasions")
    assert "3. Ravi & Neha anniversary — Sat 3 Oct (in 5 days)" in three
    everyone = await upcoming(conn_for("birthday"), "all birthdays", TODAY)
    assert everyone.startswith("All 9 birthdays")
    assert everyone.splitlines()[-1].startswith("9. Bhavesh Jhaveri — Tue 21 Sep 2027"[:26])
    assert "Send upcoming all" not in everyone


@pytest.mark.parametrize(
    "text,expected",
    [
        ("next upcoming birthday", "/upcoming upcoming birthday"),
        ("Upcoming birthdays?", "/upcoming birthdays?"),
        ("upcoming 10", "/upcoming 10"),
        ("upcoming all", "/upcoming all"),
        ("When is the next birthday?", "/upcoming birthday?"),
        ("show me next 3 anniversaries", "/upcoming 3 anniversaries"),
    ],
)
def test_natural_questions_in_self_chat(text: str, expected: str) -> None:
    assert command_text(text, self_chat=True) == expected
    assert command_text(text, self_chat=False) is None  # Other chats: private data.
