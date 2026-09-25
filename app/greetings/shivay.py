"""Shivay sendText adapter, based on this repository's original API client."""

from urllib.parse import quote

import httpx

from app.config import Settings


class DeliveryError(Exception):
    """An outcome safe to log without exposing provider responses or secrets."""

    def __init__(self, *, uncertain: bool, code: str) -> None:
        super().__init__(code)
        self.uncertain = uncertain


class ShivaySender:
    """No automatic POST retry: a timeout may happen after a message was accepted."""

    def __init__(self, config: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.path = f"message/sendText/{quote(config.shivay_instance_name, safe='')}"
        self.client = httpx.AsyncClient(
            base_url=config.shivay_api_url.rstrip("/") + "/",
            headers={"apikey": config.shivay_api_key.get_secret_value()},
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            transport=transport,
        )

    async def send_text(self, phone_number: str, text: str) -> str:
        try:
            response = await self.client.post(
                self.path, json={"number": phone_number.lstrip("+"), "text": text}
            )
        except httpx.RequestError as exc:
            raise DeliveryError(uncertain=True, code="transport_error") from exc
        if not response.is_success:
            raise DeliveryError(
                uncertain=response.status_code >= 500 or response.status_code in (408, 409),
                code=f"http_{response.status_code}",
            )
        try:
            body = response.json()
            # Shivay/Evolution sendText acknowledges acceptance with key.id.
            message_id = body.get("key", {}).get("id")
            if body.get("error") or not isinstance(message_id, str) or not message_id:
                raise ValueError("Missing message acknowledgement")
        except (ValueError, AttributeError, TypeError) as exc:
            raise DeliveryError(uncertain=True, code="unrecognized_acknowledgement") from exc
        return message_id

    async def aclose(self) -> None:
        await self.client.aclose()
