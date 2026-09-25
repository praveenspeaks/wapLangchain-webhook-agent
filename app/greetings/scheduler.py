"""Check the local calendar every minute and send each annual greeting once."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import Settings
from app.greetings.repository import GreetingRepository
from app.greetings.shivay import DeliveryError

logger = logging.getLogger(__name__)


class Sender(Protocol):
    async def send_text(self, phone_number: str, text: str) -> str: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


def due_date(now: datetime, timezone: str, send_time: str) -> date | None:
    """Allow same-day catch-up; compare local time to handle DST and date boundaries."""
    local = now.astimezone(ZoneInfo(timezone))
    return local.date() if local.time() >= time.fromisoformat(send_time) else None


def greeting_text(name: str, occasion: str) -> str:
    if occasion == "birthday":
        return f"Happy birthday, {name}! Wishing you a wonderful day and a joyful year ahead! 🎂"
    if occasion == "anniversary":
        return (
            f"Happy anniversary, {name}! Wishing you happiness and many wonderful years ahead! 🎉"
        )
    raise ValueError("Unsupported occasion")


class GreetingScheduler:
    def __init__(
        self,
        config: Settings,
        repository: GreetingRepository,
        sender: Sender,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.config = config
        self.repository = repository
        self.sender = sender
        self.clock = clock

    async def run_once(self) -> None:
        for timezone in await self.repository.timezones():
            try:
                ZoneInfo(timezone)
            except (ZoneInfoNotFoundError, ValueError):
                logger.warning("Skipping invalid recipient timezone", extra={"timezone": timezone})
                continue
            await self.run_timezone(timezone)

    async def run_timezone(self, timezone: str) -> None:
        today = due_date(self.clock(), timezone, self.config.greetings_time)
        if today is None:
            return
        for person in await self.repository.due(today, timezone):
            # Do not send yesterday's greeting if a large batch crosses local midnight.
            if due_date(self.clock(), timezone, self.config.greetings_time) != today:
                break
            text = greeting_text(person["name"], person["occasion"])
            delivery_id = await self.repository.claim(person["id"], today, timezone)
            if delivery_id is None:
                continue
            try:
                message_id = await self.sender.send_text(person["phone_number"], text)
            except DeliveryError as exc:
                status = "unknown" if exc.uncertain else "failed"
                await self.repository.finish(delivery_id, status, None)
                logger.warning(
                    "Greeting send unsuccessful",
                    extra={"delivery_id": delivery_id, "status": status, "reason": str(exc)},
                )
            except Exception as exc:
                await self.repository.finish(delivery_id, "unknown", None)
                logger.error(
                    "Greeting send outcome unknown",
                    extra={"delivery_id": delivery_id, "error_type": type(exc).__name__},
                )
            else:
                await self.repository.finish(delivery_id, "sent", message_id)
                logger.info("Greeting accepted by Shivay", extra={"delivery_id": delivery_id})

    async def run_forever(self) -> None:
        logger.info(
            "Greeting scheduler started",
            extra={
                "send_time": self.config.greetings_time,
                "timezone": "per recipient",
            },
        )
        while True:
            try:
                await self.run_once()
            except Exception as exc:
                # Leave committed claims intact if the database fails after a send.
                logger.error(
                    "Greeting scheduler check failed", extra={"error_type": type(exc).__name__}
                )
            await asyncio.sleep(60)
