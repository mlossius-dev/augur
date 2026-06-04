"""
Plain HTTP fetcher.

Fetches a URL and returns a FetchResult with the raw text body.
Used for simple HTML pages and JSON endpoints that don't need JS rendering.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
import structlog

from augur.ingestion.models import FetchResult

log = structlog.get_logger(__name__)

# Standard headers — look like a browser to avoid bot-detection on simple sites
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AugurBot/1.0; "
        "+https://github.com/mlossius-dev/augur)"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class HttpFetcher:
    """
    Async HTTP fetcher using httpx.

    One instance per ingestion cycle; shares a connection pool.
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_redirects: int = 5,
    ) -> None:
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout
        self._max_redirects = max_redirects

    async def __aenter__(self) -> HttpFetcher:
        self._client = httpx.AsyncClient(
            headers=_HEADERS,
            timeout=self._timeout,
            max_redirects=self._max_redirects,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def fetch(
        self,
        url: str,
        *,
        source_id: str,
        perspective: str,
        content_type: str = "article",
        language: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> FetchResult:
        """
        Fetch a URL and return a FetchResult.

        Raises httpx exceptions on network failure (caller should handle).
        """
        assert self._client is not None, "Use as async context manager"

        fetched_at = datetime.now(timezone.utc)
        log.debug("http.fetch", url=url, source_id=source_id)

        response = await self._client.get(url)
        response.raise_for_status()

        body = response.text
        content_timestamp = _parse_last_modified(response.headers)

        metadata: dict[str, Any] = {
            "url": url,
            "status_code": response.status_code,
            "content_hash": hashlib.sha256(body.encode()).hexdigest()[:16],
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        # Try to detect language from Content-Language header
        if language is None:
            lang_header = response.headers.get("content-language", "")
            if lang_header:
                language = lang_header.split(",")[0].strip()[:2].lower()

        return FetchResult(
            source_id=source_id,
            url=url,
            perspective=perspective,
            raw_content=body,
            fetched_at=fetched_at,
            content_timestamp=content_timestamp or fetched_at,
            content_type=content_type,
            language=language or "en",
            metadata=metadata,
        )


def _parse_last_modified(headers: httpx.Headers) -> datetime | None:
    """Parse Last-Modified header to a timezone-aware datetime."""
    value = headers.get("last-modified") or headers.get("date")
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).replace(tzinfo=timezone.utc)
    except Exception:
        return None
