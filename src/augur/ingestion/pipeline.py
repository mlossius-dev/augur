"""
Ingestion pipeline orchestrator.

Ties together: source registry → fetchers → spam filter → normalizer →
DB store → archiver.

Entry point: IngestionPipeline.run_source(source) and run_all().
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from augur.ingestion.api_clients.fred import FredClient
from augur.ingestion.api_clients.usgs import UsgsClient
from augur.ingestion.archiver import PayloadArchiver
from augur.ingestion.fetchers.rss import RssFetcher
from augur.ingestion.fetchers.searxng import SearxngFetcher
from augur.ingestion.models import FetchResult, SourceConfig
from augur.ingestion.normalizer import content_hash, normalize
from augur.ingestion.spam_filter import check as spam_check
from augur.ingestion.source_registry import get_enabled_sources

log = structlog.get_logger(__name__)


class IngestionPipeline:
    """
    Fetch, filter, normalize, and store payloads for all configured sources.

    One instance per ingestion cycle; uses the shared asyncpg pool.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        archive_root: Path | str = "/data/augur/payloads",
        searxng_url: str = "",
        sources_path: Path | str | None = None,
        s3_client=None,
        s3_bucket: str | None = None,
    ) -> None:
        self._pool = pool
        self._archiver = PayloadArchiver(
            archive_root,
            s3_client=s3_client,
            s3_bucket=s3_bucket,
        )
        self._searxng_url = searxng_url
        self._sources_path = sources_path

    async def run_all(self) -> dict[str, int]:
        """
        Run ingestion for all enabled sources.

        Returns a summary dict: {source_id: n_stored}.
        """
        sources = get_enabled_sources(self._sources_path)
        summary: dict[str, int] = {}

        log.info("ingestion.run_all_start", n_sources=len(sources))
        for source in sources:
            try:
                n = await self.run_source(source)
                summary[source.source_id] = n
            except Exception as exc:
                log.error(
                    "ingestion.source_failed",
                    source_id=source.source_id,
                    error=str(exc),
                )
                summary[source.source_id] = 0

        total = sum(summary.values())
        log.info("ingestion.run_all_done", total_stored=total, by_source=summary)
        return summary

    async def run_source(self, source: SourceConfig) -> int:
        """
        Fetch, filter, normalize, and store payloads for one source.

        Returns the number of payloads stored (rejected ones are counted
        separately in the DB's rejected=True rows).
        """
        log.info("ingestion.source_start", source_id=source.source_id, method=source.access_method)

        # Fetch
        fetch_results = await self._fetch(source)
        if not fetch_results:
            log.info("ingestion.source_empty", source_id=source.source_id)
            return 0

        # Load known hashes for this source to detect exact duplicates
        known_hashes = await self._load_known_hashes(source.source_id)

        stored = 0
        for fr in fetch_results:
            # Spam filter
            fr = spam_check(fr, known_hashes=known_hashes)

            # Normalize to DB shape
            payload = normalize(fr)

            # Store in DB (both clean and rejected payloads are stored)
            stored_ok = await self._store_payload(payload)
            if stored_ok and not fr.rejected:
                # Add to known hashes so later results in same batch deduplicate
                known_hashes.add(content_hash(fr.raw_content))
                stored += 1

                # Archive to filesystem
                try:
                    local_path = self._archiver.archive(
                        payload,
                        payload_id=payload["payload_id"],
                        content_timestamp=payload["content_timestamp"],
                        source_id=source.source_id,
                    )
                    # S3 upload is best-effort; failures are logged, not fatal
                    if self._archiver._s3 is not None:
                        self._archiver.upload_to_s3(
                            local_path,
                            source_id=source.source_id,
                            content_timestamp=payload["content_timestamp"],
                            payload_id=payload["payload_id"],
                        )
                except Exception as exc:
                    log.warning("ingestion.archive_failed", error=str(exc))

        log.info(
            "ingestion.source_done",
            source_id=source.source_id,
            fetched=len(fetch_results),
            stored=stored,
        )
        return stored

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _fetch(self, source: SourceConfig) -> list[FetchResult]:
        """Dispatch to the appropriate fetcher based on source.access_method."""
        method = source.access_method

        if method == "rss":
            fetcher = RssFetcher()
            return await fetcher.fetch_source(source)

        elif method == "api":
            source_id = source.source_id
            if source_id == "fred":
                client = FredClient()
                return await client.fetch_source(source)
            elif source_id == "usgs_earthquakes":
                client = UsgsClient()
                return await client.fetch_source(source)
            else:
                log.warning("ingestion.unknown_api_source", source_id=source_id)
                return []

        elif method == "searxng":
            if not self._searxng_url:
                log.warning("ingestion.searxng_url_missing")
                return []
            # Substitute the env var in url_base if it hasn't been expanded
            effective_url = self._searxng_url or source.url_base
            fetcher = SearxngFetcher(effective_url)
            return await fetcher.fetch_source(source)

        elif method == "http":
            # Generic HTTP: configured via access_config.urls list
            urls: list[str] = source.access_config.get("urls", [])
            from augur.ingestion.fetchers.http import HttpFetcher
            results: list[FetchResult] = []
            async with HttpFetcher() as fetcher:
                for url in urls:
                    try:
                        fr = await fetcher.fetch(
                            url,
                            source_id=source.source_id,
                            perspective=source.perspective,
                        )
                        results.append(fr)
                    except Exception as exc:
                        log.warning("ingestion.http_fetch_failed", url=url, error=str(exc))
            return results

        else:
            log.warning("ingestion.unknown_method", method=method, source_id=source.source_id)
            return []

    async def _load_known_hashes(self, source_id: str) -> set[str]:
        """
        Load content hashes of recently stored payloads for `source_id`.

        Looks back 7 days to catch near-duplicates across ingestion cycles.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT metadata->>'content_hash' AS h
                FROM payloads
                WHERE source_id = $1
                  AND fetched_at > now() - interval '7 days'
                  AND metadata->>'content_hash' IS NOT NULL
                """,
                source_id,
            )
        return {r["h"] for r in rows if r["h"]}

    async def _store_payload(self, payload: dict[str, Any]) -> bool:
        """
        Insert a payload into the DB.

        Returns True if inserted; False if a unique conflict was detected
        (should not happen often since we pre-check, but harmless).
        """
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO payloads
                        (payload_id, source_id, fetched_at, content_timestamp,
                         perspective, content, content_type, language,
                         metadata, rejected, rejected_reason)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    payload["payload_id"],
                    payload["source_id"],
                    payload["fetched_at"],
                    payload["content_timestamp"],
                    payload["perspective"],
                    payload["content"],
                    payload["content_type"],
                    payload["language"],
                    json.dumps(payload["metadata"]),
                    payload["rejected"],
                    payload["rejected_reason"],
                )
                return True
            except asyncpg.UniqueViolationError:
                return False
