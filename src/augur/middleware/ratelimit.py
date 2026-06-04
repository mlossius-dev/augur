"""
Sliding-window rate limiter for the conversation endpoint.

Uses an in-process token bucket per IP address.  Suitable for a single-
process deployment (one uvicorn worker).  For multi-process deployments,
replace with a Redis-backed implementation.

Configured via:
  CONV_RATE_LIMIT_PER_MINUTE  (default 10)
  CONV_RATE_LIMIT_BURST       (default 3)

Rate limiting is only applied to POST /api/conversation/query.
All other paths pass through without inspection.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_RATE_LIMITED_PATH = "/api/conversation/query"


@dataclass
class _Bucket:
    timestamps: deque = field(default_factory=deque)


class ConversationRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiter.

    Allows at most `per_minute` requests per IP in any 60-second window.
    """

    def __init__(self, app, *, per_minute: int = 10) -> None:
        super().__init__(app)
        self._per_minute = per_minute
        self._window = 60.0
        self._buckets: dict[str, _Bucket] = defaultdict(_Bucket)

    def _client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _is_allowed(self, ip: str) -> tuple[bool, int]:
        now = time.monotonic()
        bucket = self._buckets[ip]

        # Evict expired timestamps
        cutoff = now - self._window
        while bucket.timestamps and bucket.timestamps[0] < cutoff:
            bucket.timestamps.popleft()

        if len(bucket.timestamps) >= self._per_minute:
            retry_after = int(self._window - (now - bucket.timestamps[0])) + 1
            return False, retry_after

        bucket.timestamps.append(now)
        return True, 0

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path != _RATE_LIMITED_PATH or request.method != "POST":
            return await call_next(request)

        ip = self._client_ip(request)
        allowed, retry_after = self._is_allowed(ip)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again shortly."},
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
