"""
shivay_client.py
----------------
Async HTTP client for the Shivay API (WhatsApp gateway).

All public methods are coroutines and raise ShivayAPIError on failure.
The client uses tenacity for automatic retries on transient errors.

Usage
-----
    client = ShivayClient()
    await client.send_text("15551234567", "Hello from the bot!")
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from models import settings

logger = logging.getLogger(__name__)


class ShivayAPIError(Exception):
    """Raised when the Shivay API returns an error or is unreachable."""


class ShivayClient:
    """
    Thin async wrapper around the Shivay API REST endpoints.

    Creates a shared ``httpx.AsyncClient`` on first use; close it explicitly
    via ``aclose()`` or by using the async context manager.
    """

    def __init__(self) -> None:
        self._base_url = settings.shivay_api_url.rstrip("/")
        self._api_key = settings.shivay_api_key
        self._instance = settings.shivay_instance_name
        self._token = settings.shivay_instance_token
        self._client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Return (or lazily create) the shared HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Content-Type": "application/json",
                    "apikey": self._api_key,
                },
                timeout=httpx.Timeout(30.0, connect=10.0),
                verify=False,  # noqa: S501 — hosted API may use self-signed cert
            )
        return self._client

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aenter__(self) -> ShivayClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST request with automatic retry on 5xx / network errors."""
        client = await self._get_client()
        try:
            response = await client.post(path, json=payload)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "Shivay API HTTP error",
                extra={"path": path, "status": exc.response.status_code, "body": exc.response.text},
            )
            raise ShivayAPIError(
                f"Shivay API returned {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            logger.error("Shivay API request error", extra={"path": path, "error": str(exc)})
            raise ShivayAPIError(f"Shivay API request failed: {exc}") from exc

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET request with automatic retry."""
        client = await self._get_client()
        try:
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise ShivayAPIError(
                f"Shivay API returned {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise ShivayAPIError(f"Shivay API request failed: {exc}") from exc

    def _jid(self, phone: str) -> str:
        """
        Normalise a phone number to a WhatsApp JID.

        Strips leading '+' and appends '@s.whatsapp.net' if not already a JID.
        """
        phone = phone.lstrip("+").replace(" ", "").replace("-", "")
        if "@" not in phone:
            phone = f"{phone}@s.whatsapp.net"
        return phone

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send_text(self, to: str, message: str) -> dict[str, Any]:
        """Send a plain-text message to a WhatsApp number or JID."""
        logger.info("Sending text message", extra={"to": to, "length": len(message)})
        # If already a full JID (contains @), pass as-is; otherwise clean up
        number = to if "@" in to else to.lstrip("+").replace(" ", "").replace("-", "")
        return await self._post(
            f"/message/sendText/{self._instance}",
            {
                "number": number,
                "text": message,
            },
        )

    async def send_buttons(
        self,
        to: str,
        title: str,
        buttons: list[dict[str, str]],
        footer: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """
        Send an interactive button message.

        Args:
            to:          Recipient phone number.
            title:       Message title shown in bold.
            buttons:     List of dicts: [{"buttonId": "1", "buttonText": {"displayText": "Yes"}}]
            footer:      Optional footer text.
            description: Optional body text below the title.
        """
        return await self._post(
            f"/message/sendButtons/{self._instance}",
            {
                "number": self._jid(to),
                "buttonMessage": {
                    "title": title,
                    "description": description,
                    "footerText": footer,
                    "buttons": buttons,
                    "headerType": 1,
                },
            },
        )

    async def send_list(
        self,
        to: str,
        title: str,
        sections: list[dict[str, Any]],
        button_text: str = "View Options",
        description: str = "",
        footer: str = "",
    ) -> dict[str, Any]:
        """
        Send a list message with selectable items.

        Args:
            to:           Recipient phone number.
            title:        Message title.
            sections:     List of section dicts with ``title`` and ``rows``.
            button_text:  Label for the list-opener button.
            description:  Body text.
            footer:       Footer text.
        """
        return await self._post(
            f"/message/sendList/{self._instance}",
            {
                "number": self._jid(to),
                "listMessage": {
                    "title": title,
                    "description": description,
                    "footerText": footer,
                    "buttonText": button_text,
                    "sections": sections,
                },
            },
        )

    async def send_media(
        self,
        to: str,
        media_url: str,
        caption: str = "",
        media_type: str = "image",
    ) -> dict[str, Any]:
        """
        Send a media message (image, video, document, audio).

        Args:
            to:         Recipient phone number.
            media_url:  Publicly accessible URL of the file.
            caption:    Optional caption shown below the media.
            media_type: "image" | "video" | "document" | "audio"
        """
        return await self._post(
            f"/message/sendMedia/{self._instance}",
            {
                "number": self._jid(to),
                "mediaMessage": {
                    "mediatype": media_type,
                    "media": media_url,
                    "caption": caption,
                },
            },
        )

    # ------------------------------------------------------------------
    # Instance management
    # ------------------------------------------------------------------

    async def create_instance(self) -> dict[str, Any]:
        """Create a new Shivay API instance (idempotent)."""
        return await self._post(
            "/instance/create",
            {
                "instanceName": self._instance,
                "token": self._token,
                "qrcode": True,
                "integration": "WHATSAPP-BAILEYS",
            },
        )

    async def get_qrcode(self) -> dict[str, Any]:
        """Fetch the QR code needed to pair a WhatsApp account."""
        return await self._get(f"/instance/connect/{self._instance}")

    async def get_instance_status(self) -> dict[str, Any]:
        """Return the current connection state of the instance."""
        return await self._get(f"/instance/connectionState/{self._instance}")

    async def set_webhook(
        self,
        webhook_url: str,
        events: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Configure the webhook URL and event subscriptions for the instance.

        Args:
            webhook_url: Publicly reachable URL to receive events.
            events:      Shivay API event names to subscribe to.
        """
        if events is None:
            events = [
                "MESSAGES_UPSERT",
                "CONNECTION_UPDATE",
                "QRCODE_UPDATED",
            ]
        return await self._post(
            f"/webhook/set/{self._instance}",
            {
                "enabled": True,
                "url": webhook_url,
                "webhookByEvents": True,
                "events": events,
            },
        )

    async def send_typing(self, to: str, duration_ms: int = 2000) -> dict[str, Any]:
        """Show a 'typing...' indicator. Silently skipped if not supported."""
        # No retry — best-effort only; some API versions lack this endpoint.
        client = await self._get_client()
        try:
            response = await client.post(
                f"/message/sendPresence/{self._instance}",
                json={
                    "number": to.lstrip("+").replace(" ", "").replace("-", ""),
                    "presence": "composing",
                    "delay": duration_ms,
                },
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            logger.debug("sendPresence not supported, skipping typing indicator")
            return {}


# ---------------------------------------------------------------------------
# Module-level singleton  (import this in server.py / agent.py)
# ---------------------------------------------------------------------------
shivay_client = ShivayClient()
