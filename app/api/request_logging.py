"""Log webhook arrivals and outcomes without message bodies or credentials."""

import logging
import time
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response

from app.api.payload_logging import payload_diagnostics
from app.config import settings

logger = logging.getLogger(__name__)
ENDPOINTS = {"/invoke", "/webhook", "/webhook/shivay"}


async def log_webhook_request(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Include unknown POST destinations to expose an incorrectly configured route.
    # Don't log arbitrary paths, query strings, headers, payloads or exception text.
    if request.method != "POST" and request.url.path not in ENDPOINTS:
        return await call_next(request)
    request_id = uuid4().hex
    context = {
        "request_id": request_id,
        "endpoint": request.url.path if request.url.path in ENDPOINTS else "unrecognized",
        "method": request.method,
    }
    started = time.monotonic()
    logger.info("Webhook request arrived", extra=context)
    try:
        if settings.webhook_log_payloads and request.url.path in ENDPOINTS:
            logger.info(
                "Webhook payload diagnostic",
                extra={
                    **context,
                    **await payload_diagnostics(request),
                },
            )
        response = await call_next(request)
    except Exception as exc:
        logger.error(
            "Webhook request failed",
            extra={
                **context,
                "status_code": 500,
                "error_type": type(exc).__name__,
            },
        )
        raise
    reasons = {
        401: "authentication_failed",
        403: "instance_or_access_rejected",
        404: "route_not_found",
        405: "wrong_http_method",
        422: "invalid_payload",
        503: "feature_disabled_or_unavailable",
    }
    outcome = reasons.get(response.status_code) or getattr(
        request.state, "webhook_outcome", "completed"
    )
    logger.info(
        "Webhook request finished",
        extra={
            **context,
            "status_code": response.status_code,
            "outcome": outcome,
            "duration_ms": round((time.monotonic() - started) * 1000),
        },
    )
    response.headers["X-Agent-Request-ID"] = request_id
    return response
