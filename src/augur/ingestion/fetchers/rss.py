"""
RSS/Atom feed fetcher.

Fetches one or more RSS/Atom feeds and returns a FetchResult per entry.
Uses feedparser for parsing; httpx for fetching (so we control headers
and timeouts consistently).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from calendar import timegm
from typing import Any

import feedparser
import httpx
import structlog

from augur.ingestion.models import FetchResult, SourceConfig

log = structlog.get_logger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AugurBot/1.0; "
        "+https://github.com/mlossius-dev/augur)"
    ),
}


class RssFetcher:
    """Fetch all configured feeds for an RSS source."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def fetch_source(
        self, source: SourceConfig
    ) -> list[FetchResult]:
        """
        Fetch all feeds configured for `source`.

        Returns one FetchResult per feed entry, up to a sensible maximum
        per feed (50 entries).
        """
        feeds: list[dict[str, Any]] = source.access_config.get("feeds", [])
        results: list[FetchResult] = []

        async with httpx.AsyncClient(
            headers=_HEADERS, timeout=self._timeout, follow_redirects=True
        ) as client:
            for feed_entry in feeds:
                feed_url: str = feed_entry.get("url", "")
                if not feed_url:
                    continue
                label: str = feed_entry.get("label", "")
                fetched_at = datetime.now(timezone.utc)

                try:
                    response = await client.get(feed_url)
                    response.raise_for_status()
                except Exception as exc:
                    log.warning(
                        "rss.fetch_failed",
                        source_id=source.source_id,
                        feed_url=feed_url,
                        error=str(exc),
                    )
                    continue

                results.extend(parse_feed_entries(
                    response.text, source=source, feed_url=feed_url,
                    label=label, fetched_at=fetched_at,
                ))

        log.info(
            "rss.fetched",
            source_id=source.source_id,
            n_feeds=len(feeds),
            n_entries=len(results),
        )
        return results


def parse_feed_entries(
    text: str,
    *,
    source: SourceConfig,
    feed_url: str,
    label: str,
    fetched_at: datetime,
    limit: int = 50,
) -> list[FetchResult]:
    """
    One FetchResult per entry of an RSS/Atom document, headline and lead.

    Shared by live ingestion and the historical backfill, which reads the same
    feeds from archived snapshots (docs/augur-sources.md, "Archive access").
    """
    results: list[FetchResult] = []
    for entry in feedparser.parse(text).get("entries", [])[:limit]:
        url = entry.get("link", "")
        title = entry.get("title", "")
        summary = entry.get("summary", "") or entry.get("content", [{}])[0].get("value", "")
        content = f"{title}\n\n{summary}".strip()
        if not content:
            continue

        pub_dt = _parse_pub_date(entry)
        metadata: dict[str, Any] = {
            "url": url,
            "headline": title,
            "feed_label": label,
            "feed_url": feed_url,
            "source_native_id": entry.get("id", url),
            "content_hash": hashlib.sha256(content.encode()).hexdigest()[:16],
        }
        results.append(
            FetchResult(
                source_id=source.source_id,
                url=url or feed_url,
                perspective=source.perspective,
                raw_content=content,
                fetched_at=fetched_at,
                content_timestamp=pub_dt or fetched_at,
                content_type="article",
                language=source.languages[0] if source.languages else "en",
                metadata=metadata,
            )
        )
    return results


def _parse_pub_date(entry: dict[str, Any]) -> datetime | None:
    """Extract publication datetime from a feedparser entry."""
    # feedparser normalizes dates to a 9-tuple or None
    published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if published_parsed is not None:
        try:
            # feedparser's struct is UTC; mktime would read it as local time
            ts = timegm(published_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass
    return None
