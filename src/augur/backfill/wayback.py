"""
Archived snapshots of admitted feeds, from the Internet Archive's Wayback Machine.

docs/augur-sources.md, "Archive access": an admitted RSS source may be read
from snapshots of its own feed.  For one day this lists the feed's snapshots
around that day, reads each one with the live parser, and keeps the items
published on the day.  Same source, perspective and format as live ingestion.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone

import httpx
import structlog

from augur.ingestion.fetchers.rss import parse_feed_entries
from augur.ingestion.models import FetchResult, SourceConfig

log = structlog.get_logger(__name__)

CDX_URL = "http://web.archive.org/cdx/search/cdx"
SNAPSHOT_URL = "http://web.archive.org/web/{timestamp}id_/{url}"  # id_: the feed as archived

# A snapshot carries roughly the last ten hours of a busy feed; reading the
# ones nearest these times covers the day and its last hours.
_TARGET_HOURS = (6, 12, 18, 24)
# Between requests to the archive, to stay a polite client
_PAUSE_SECONDS = 1.0


def pick_snapshots(timestamps: list[str], day: date) -> list[str]:
    """The snapshot nearest each target time of `day` (deduplicated, in order)."""
    if not timestamps:
        return []
    parsed = {
        ts: datetime.strptime(ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        for ts in timestamps
    }
    start = datetime.combine(day, time(0), tzinfo=timezone.utc)
    chosen: list[str] = []
    for hour in _TARGET_HOURS:
        target = start + timedelta(hours=hour)
        nearest = min(parsed, key=lambda ts: abs(parsed[ts] - target))
        if abs(parsed[nearest] - target) <= timedelta(hours=6) and nearest not in chosen:
            chosen.append(nearest)
    return sorted(chosen)


def items_published_on(results: list[FetchResult], day: date) -> list[FetchResult]:
    """Items whose publication date (UTC) is `day`, first occurrence of each link kept."""
    seen: set[str] = set()
    kept: list[FetchResult] = []
    for r in results:
        if r.content_timestamp.astimezone(timezone.utc).date() != day:
            continue
        key = r.url or r.raw_content
        if key in seen:
            continue
        seen.add(key)
        kept.append(r)
    return kept


class WaybackFeedFetcher:
    """Read one day of a source's feeds from archived snapshots."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _snapshot_timestamps(self, feed_url: str, day: date) -> list[str]:
        params = {
            "url": feed_url,
            "from": day.strftime("%Y%m%d"),
            "to": (day + timedelta(days=1)).strftime("%Y%m%d"),
            "output": "json",
            "fl": "timestamp",
            "filter": "statuscode:200",
            "collapse": "timestamp:10",  # at most one per hour
        }
        response = await self._client.get(CDX_URL, params=params)
        response.raise_for_status()
        rows = response.json() if response.text.strip() else []
        return [row[0] for row in rows[1:]]  # first row is the header

    async def fetch_day(self, source: SourceConfig, day: date) -> list[FetchResult]:
        """Items the source's feeds published on `day`, as seen in archived snapshots."""
        results: list[FetchResult] = []
        for feed in source.access_config.get("feeds", []):
            feed_url = feed.get("url", "")
            if not feed_url:
                continue
            try:
                timestamps = await self._snapshot_timestamps(feed_url, day)
            except Exception as exc:
                log.warning("wayback.cdx_failed", source_id=source.source_id,
                            feed_url=feed_url, day=str(day), error=str(exc))
                continue
            for ts in pick_snapshots(timestamps, day):
                await asyncio.sleep(_PAUSE_SECONDS)
                try:
                    response = await self._client.get(
                        SNAPSHOT_URL.format(timestamp=ts, url=feed_url))
                    response.raise_for_status()
                except Exception as exc:
                    log.warning("wayback.snapshot_failed", source_id=source.source_id,
                                snapshot=ts, error=str(exc))
                    continue
                for r in parse_feed_entries(
                    response.text, source=source, feed_url=feed_url,
                    label=feed.get("label", ""), fetched_at=datetime.now(timezone.utc),
                ):
                    # Live extraction skips these (scheduler); the snapshot is provenance
                    r.metadata["backfill"] = True
                    r.metadata["wayback_snapshot"] = ts
                    results.append(r)
        kept = items_published_on(results, day)
        log.info("wayback.day_fetched", source_id=source.source_id, day=str(day),
                 n_items=len(kept))
        return kept
