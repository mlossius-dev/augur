"""
SearXNG-mediated search fetcher.

SearXNG is a discovery layer, not a source in its own right.
Queries return result URLs + snippets; the fetcher returns FetchResults
from the snippet text so we get *something* without fetching each full article
(which would hammer every site listed in results).

Full-text fetching of SearXNG results is handled later in the pipeline if the
snippet signals are strong enough to warrant it (Phase 3+).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from augur.ingestion.models import FetchResult, SourceConfig

log = structlog.get_logger(__name__)


class SearxngFetcher:
    """Fetch search results from a SearXNG instance."""

    def __init__(self, searxng_url: str, timeout: float = 30.0) -> None:
        # Strip trailing slash
        self._base = searxng_url.rstrip("/")
        self._timeout = timeout

    async def fetch_source(self, source: SourceConfig) -> list[FetchResult]:
        """
        Execute all queries configured for `source`.

        Returns one FetchResult per search result whose snippet is non-empty.
        The raw_content is `{title}\n\n{snippet}` — sufficient for the
        commodities lens to determine relevance.
        """
        queries: list[dict[str, Any]] = source.access_config.get("queries", [])
        max_results = int(source.access_config.get("max_results_per_query", 10))
        results: list[FetchResult] = []

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for query_cfg in queries:
                topic = query_cfg.get("topic", "")
                categories = ",".join(query_cfg.get("categories", ["news"]))
                if not topic:
                    continue

                params = {
                    "q": topic,
                    "format": "json",
                    "categories": categories,
                    "language": "en-US",
                    "pageno": 1,
                }
                url = f"{self._base}/search?{urlencode(params)}"

                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    data = response.json()
                except Exception as exc:
                    log.warning(
                        "searxng.fetch_failed",
                        query=topic,
                        error=str(exc),
                    )
                    continue

                fetched_at = datetime.now(timezone.utc)
                for item in data.get("results", [])[:max_results]:
                    title = item.get("title", "")
                    snippet = item.get("content", "")
                    result_url = item.get("url", "")

                    content = f"{title}\n\n{snippet}".strip()
                    if not content:
                        continue

                    # Attempt to parse the result's publishedDate
                    published_str = item.get("publishedDate")
                    content_ts: datetime | None = None
                    if published_str:
                        try:
                            from email.utils import parsedate_to_datetime
                            content_ts = parsedate_to_datetime(published_str)
                        except Exception:
                            pass

                    results.append(
                        FetchResult(
                            source_id=source.source_id,
                            url=result_url,
                            perspective=source.perspective,
                            raw_content=content,
                            fetched_at=fetched_at,
                            content_timestamp=content_ts or fetched_at,
                            content_type="article",
                            language="en",
                            metadata={
                                "url": result_url,
                                "headline": title,
                                "query": topic,
                                "searxng_engine": item.get("engine", ""),
                                "source_native_id": result_url,
                            },
                        )
                    )

        log.info(
            "searxng.fetched",
            source_id=source.source_id,
            n_queries=len(queries),
            n_results=len(results),
        )
        return results
