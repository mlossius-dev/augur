"""
API key authentication middleware.

If AUGUR_API_KEY is set in the environment, every request to /api/*
must include a matching X-API-Key header.  Health endpoints (/health,
/docs, /openapi.json) and the root (/) are always exempt.

In development (AUGUR_API_KEY unset), all requests pass through.
"""

from __future__ import annotations

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

_EXEMPT_PREFIXES = ("/health", "/docs", "/openapi.json", "/redoc", "/static", "/")


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, api_key: str | None) -> None:
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next) -> Response:
        if self._api_key is None:
            # No key configured — open access (development mode)
            return await call_next(request)

        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        provided = request.headers.get("X-API-Key", "")
        if provided != self._api_key:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing X-API-Key header"},
            )

        return await call_next(request)
