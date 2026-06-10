"""TimingMiddleware + CorrelationMiddleware for FastAPI.

TimingMiddleware:    adds X-Process-Time header to every response
CorrelationMiddleware: injects X-Correlation-ID into request/response
"""
from __future__ import annotations
import time
import uuid
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


class TimingMiddleware(BaseHTTPMiddleware):
    """Adds X-Process-Time (ms) header and logs slow requests."""

    SLOW_THRESHOLD_MS = 2_000   # log warning above this

    async def dispatch(self, request: Request, call_next) -> Response:
        t0 = time.monotonic()
        response = await call_next(request)
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        response.headers["X-Process-Time"] = str(elapsed_ms)

        level = logging.WARNING if elapsed_ms > self.SLOW_THRESHOLD_MS else logging.DEBUG
        logger.log(
            level,
            "%s %s → %d (%dms)",
            request.method, request.url.path, response.status_code, elapsed_ms,
        )
        return response


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Propagates or generates X-Correlation-ID header."""

    HEADER = "X-Correlation-ID"

    async def dispatch(self, request: Request, call_next) -> Response:
        correlation_id = request.headers.get(self.HEADER) or str(uuid.uuid4())
        request.state.correlation_id = correlation_id

        response = await call_next(request)
        response.headers[self.HEADER] = correlation_id
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds basic security headers to every response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options":  "nosniff",
            "X-Frame-Options":         "DENY",
            "Referrer-Policy":         "strict-origin-when-cross-origin",
        })
        return response
