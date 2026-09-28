"""Opt-in JSON diagnostics with credential redaction and bounded log size."""

import json
import secrets
from typing import Any

from fastapi import Request

from app.config import settings

REDACTED = "[REDACTED]"


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 25:
        return "[depth limit]"
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = "".join(c for c in key.lower() if c.isalnum())
            if any(
                word in normalized
                for word in (
                    "apikey",
                    "secret",
                    "token",
                    "password",
                    "authorization",
                    "cookie",
                    "credential",
                    "mediaKey".lower(),
                    "privatekey",
                )
            ) or normalized in {"headers", "query", "params"}:
                result[key] = REDACTED
            else:
                result[key] = redact(item, depth + 1)
        return result
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        return "[URL REDACTED]"
    return value


async def payload_diagnostics(request: Request) -> dict[str, Any]:
    body = await request.body()  # Starlette preserves it for downstream handlers.
    header = request.headers.get("X-Webhook-Secret")
    expected = settings.shivay_webhook_secret.get_secret_value()
    result: dict[str, Any] = {
        "body_bytes": len(body),
        "webhook_secret_header_present": header is not None,
        "webhook_secret_header_matches": bool(header and expected)
        and secrets.compare_digest((header or "").encode(), expected.encode()),
    }
    if len(body) > 65536:
        return result | {"payload": "[omitted: exceeds 65536 bytes]"}
    try:
        parsed = json.loads(body)
        result["payload"] = redact(parsed)
    except (ValueError, UnicodeError, RecursionError):
        return result | {"payload": "[omitted: invalid or excessively nested JSON]"}
    event = parsed[0] if isinstance(parsed, list) and len(parsed) == 1 else parsed
    if isinstance(event, dict) and "body" in event:
        event = event["body"]
    api_key = event.get("apikey") if isinstance(event, dict) else None
    configured = settings.shivay_api_key.get_secret_value()
    result["payload_apikey_present"] = api_key is not None
    result["payload_apikey_matches"] = (
        isinstance(api_key, str)
        and bool(api_key and configured)
        and secrets.compare_digest(api_key.encode(), configured.encode())
    )
    return result
