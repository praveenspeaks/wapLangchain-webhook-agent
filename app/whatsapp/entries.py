"""Allowlisted data-entry schemas. Model output never becomes executable SQL."""

import json
import re
from datetime import date
from decimal import Decimal
from functools import cache
from importlib import resources
from typing import Annotated, Any, Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.greetings.schemas import OccasionInput

Name = Annotated[str, Field(min_length=1, max_length=200)]
Category = Annotated[str, Field(min_length=1, max_length=100)]
Location = Annotated[str, Field(min_length=1, max_length=1000)]
Phone = Annotated[str, Field(pattern=r"^\+[1-9][0-9]{7,14}$")]
Money = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Product(Entry):
    name: Name
    description: str = Field(default="", max_length=4000)
    price: Money
    stock: int = Field(ge=0)
    category: Category


class Order(Entry):
    id: str = Field(pattern=r"^ORD-[A-Z0-9-]+$", max_length=20)
    customer_phone: Phone
    status: Literal["pending", "paid", "shipped", "delivered", "cancelled"] = "pending"
    total_amount: Money = Decimal("0")


class OrderItem(Entry):
    order_id: str = Field(pattern=r"^ORD-[A-Z0-9-]+$", max_length=20)
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    unit_price: Money


class SupportTicket(Entry):
    id: str = Field(pattern=r"^TKT-[A-Z0-9-]+$", max_length=20)
    customer_phone: Phone
    issue: str = Field(min_length=1, max_length=4000)
    priority: Literal["Low", "Normal", "High", "Urgent"] = "Normal"
    status: Literal["Open", "In Progress", "Resolved", "Closed"] = "Open"


class Event(Entry):
    event_name: Name
    event_date: date
    venue: Name
    total_tickets: int = Field(gt=0)
    tickets_sold: int = Field(default=0, ge=0)
    price: Money
    category: Category

    @model_validator(mode="after")
    def valid_capacity(self) -> "Event":
        if self.tickets_sold > self.total_tickets:
            raise ValueError("tickets_sold must not exceed total_tickets")
        return self


class Service(Entry):
    name: Name
    category: Category
    phone_number: Phone
    location: Location
    description: str = Field(default="", max_length=4000)


class Restaurant(Entry):
    name: Name
    location: Location
    cuisine: Category | None = None
    phone_number: Phone | None = None
    description: str = Field(default="", max_length=4000)


class Place(Entry):
    name: Name
    location: Location
    category: Category
    description: str = Field(default="", max_length=4000)


ENTITIES: dict[str, tuple[str, type[BaseModel]]] = {
    "product": ("products", Product),
    "order": ("orders", Order),
    "order_item": ("order_items", OrderItem),
    "support_ticket": ("support_tickets", SupportTicket),
    "event": ("event_tickets", Event),
    "service": ("services", Service),
    "restaurant": ("restaurants", Restaurant),
    "place": ("places_to_visit", Place),
    "occasion": ("greeting_occasions", OccasionInput),
}
ALIASES = {
    "products": "product",
    "orders": "order",
    "order_items": "order_item",
    "support_tickets": "support_ticket",
    "events": "event",
    "services": "service",
    "restaurants": "restaurant",
    "places": "place",
    "places_to_visit": "place",
    "occasions": "occasion",
    # Common owner typos, and occasion kinds used as the type ("add birthday ...").
    "occassion": "occasion",
    "occassions": "occasion",
    "ocassion": "occasion",
    "ocasion": "occasion",
    "occation": "occasion",
    "birthday": "occasion",
    "anniversary": "occasion",
    "resturant": "restaurant",
    "restaurent": "restaurant",
}
COMMANDS = {"/add", "/set", "/save", "/cancel", "/draft", "/help", "/birthdays", "/dismiss"}


def entity_name(value: str) -> str:
    value = value.lower()
    return ALIASES.get(value, value)


def clean_phone(value: str) -> str:
    """'91 98765 43210' or '0091-9876543210' -> '+919876543210'; local numbers stay invalid."""
    digits = re.sub(r"[\s().-]", "", value)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    elif re.fullmatch(r"[1-9][0-9]{7,14}", digits):
        digits = "+" + digits
    return digits


def clean_timezone(value: str) -> str:
    """'london' or 'new york' -> the unique IANA zone with that city, else unchanged."""
    city = value.strip().replace(" ", "_").lower()
    zones = {zone.lower(): zone for zone in available_timezones()}
    if city in zones:
        return zones[city]
    matches = [zone for key, zone in zones.items() if key.endswith("/" + city)]
    return matches[0] if len(matches) == 1 else value


@cache
def zone_countries() -> dict[str, str]:
    """IANA zone -> ISO country code, from the bundled tzdata zone.tab."""
    try:
        table = resources.files("tzdata").joinpath("zoneinfo", "zone.tab").read_text("utf-8")
    except (ModuleNotFoundError, OSError):
        return {}
    rows = (line.split("\t") for line in table.splitlines() if line and line[0] != "#")
    return {row[2]: row[0] for row in rows if len(row) >= 3}


def clean_fields(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize owner-typed values before validation; never invent unrelated ones.

    A missing country is taken from the recipient's timezone (Europe/London -> GB),
    because that timezone already names exactly one country.
    """
    data = dict(data)
    for field in ("phone_number", "customer_phone"):
        if isinstance(data.get(field), str):
            data[field] = clean_phone(data[field])
    if isinstance(data.get("timezone"), str):
        data["timezone"] = clean_timezone(data["timezone"])
    if isinstance(data.get("country"), str):
        data["country"] = data["country"].strip().upper()
    if not data.get("country") and data.get("timezone") in zone_countries():
        data["country"] = zone_countries()[data["timezone"]]
    return data


def command_text(text: str, self_chat: bool = False) -> str | None:
    """Canonical owner command, or None for ordinary messages.

    Accepts slash commands and plain "add TYPE details" (e.g. "Add occasion
    birthday of ..."), but only when TYPE is a known record type. In the owner's
    "message yourself" chat, "save", "cancel", "draft", "help" and "set ..." also
    work without the slash; elsewhere those everyday words stay ordinary text.
    """
    text = text.strip()
    word, _, rest = text.partition(" ")
    if word.lower() in COMMANDS:
        return text
    plain = word.lower().rstrip(".!")
    if self_chat and "/" + plain in COMMANDS - {"/add"}:
        return ("/" + plain + " " + rest.strip()).strip()
    entity = rest.strip().split(" ", 1)[0].strip(",.:;")
    if word.lower() == "add" and entity_name(entity) in ENTITIES:
        return "/add " + rest.strip()
    return None


def split_add(arguments: str) -> tuple[str, str]:
    """Split "/add" arguments into an entity and its details."""
    word, _, supplied = arguments.strip().partition(" ")
    word = word.strip(",.:;")
    if word.lower() in ("birthday", "anniversary"):
        # The occasion kind is itself a field value, so keep it in the details.
        supplied = f"{word} {supplied}"
    return entity_name(word), supplied


EXAMPLES = {
    "occasion": "add birthday of Asha Rao, 16 October, +91 98765 43210, country IN, "
    "timezone London",
    "restaurant": "add restaurant The Olive Tree, Richmond, London, cuisine Mediterranean",
    "service": "add service Sam, plumber, +447700900123, Richmond",
    "place": "add place Kew Gardens, Richmond, category park",
    "event": "add event Diwali Night on 2026-11-08 at Town Hall, 200 tickets, price 15, "
    "category festival",
    "product": "add product Blue Mug, price 8.50, stock 40, category kitchen",
    "order": "add order ORD-1001 for +447700900123",
    "order_item": "add order_item order ORD-1001, product 3, quantity 2, unit price 8.50",
    "support_ticket": "add support_ticket TKT-2001, +447700900123, parcel arrived damaged",
}

HELP = (
    "Commands (send from your own number):\n"
    "add TYPE details - start a draft\n"
    "/set details - add missing fields or correct the draft\n"
    "/draft - show the current draft\n"
    "/save - create the record\n"
    "/cancel - discard the draft\n"
    "/help TYPE - fields and an example for one type\n"
    "/birthdays - birthday/anniversary wishes you sent, captured to add\n"
    "add birthday N - draft captured wish N; /dismiss N - remove it from the list\n"
    "In your message-yourself chat the / is optional; in other chats only add works "
    "without it.\n\n"
    "Types: " + ", ".join(ENTITIES) + " (birthday and anniversary mean occasion)."
)


def help_text(topic: str = "") -> str:
    """General help, or required/optional fields and an example for one type."""
    if not topic.strip():
        return HELP
    entity = split_add(topic)[0]
    if entity not in ENTITIES:
        return f"Unknown type '{topic.strip()}'.\n\n{HELP}"
    # Inherited fields (timezone) come first in the model; list them last for reading.
    fields = dict(
        sorted(ENTITIES[entity][1].model_fields.items(), key=lambda f: f[0] == "timezone")
    )
    required = [name for name, field in fields.items() if field.is_required()]
    optional = [name for name, field in fields.items() if not field.is_required()]
    lines = [f"{entity}", "Required: " + ", ".join(required)]
    if optional:
        lines.append("Optional: " + ", ".join(optional))
    lines += [
        "Phone numbers need + and country code; dates are YYYY-MM-DD.",
        "",
        "Example:",
        EXAMPLES[entity],
        "/save",
    ]
    return "\n".join(lines)


def validate_entry(entity: str, data: dict[str, Any]) -> BaseModel:
    model = ENTITIES[entity][1]
    extra = set(data) - set(model.model_fields)
    if extra:
        raise ValueError("Unknown fields: " + ", ".join(sorted(extra)))
    return model.model_validate(data)


def review(entity: str, data: dict[str, Any]) -> str:
    shown = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    try:
        validated = validate_entry(entity, data)
    except ValidationError as exc:
        issues = []
        for error in exc.errors(include_input=False, include_url=False):
            field = ".".join(str(part) for part in error["loc"]) or "record"
            issues.append(f"- {field}: {error['msg']}")
        return (
            f"Draft {entity}:\n{shown}\nMissing or invalid fields:\n"
            + "\n".join(issues)
            + "\nUse /set with the missing/corrected values."
        )
    except ValueError as exc:
        return f"Draft {entity}:\n{shown}\n{exc}\nUse /set to correct the draft."
    complete = json.dumps(validated.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return (
        f"Review {entity}:\n{complete}\n"
        "Send /save to create this record, /set to correct it, or /cancel."
    )
