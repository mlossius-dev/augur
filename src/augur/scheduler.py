"""
APScheduler configuration for the Augur pipeline.

Two recurring jobs:
  - ingestion_job:  runs every hour, fetches new payloads from all sources
  - extraction_job: runs every hour (offset by 10 min), extracts signals
                    from recently ingested payloads

Jobs are registered on the FastAPI app lifespan and share the application's
asyncpg pool.

APScheduler docs: https://apscheduler.readthedocs.io/en/3.x/
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

log = structlog.get_logger(__name__)

# ── Job implementations ───────────────────────────────────────────────────────


async def _ingestion_job(app_state: Any) -> None:
    """Hourly ingestion: fetch payloads from all enabled sources."""
    from augur.ingestion.pipeline import IngestionPipeline
    from augur.config import get_settings

    settings = get_settings()
    pool = app_state.raw_pool

    pipeline = IngestionPipeline(
        pool,
        archive_root=settings.payload_archive_root,
        searxng_url=str(settings.searxng_url) if settings.searxng_url else "",
    )

    try:
        summary = await pipeline.run_all()
        log.info("scheduler.ingestion_done", summary=summary)
    except Exception as exc:
        log.error("scheduler.ingestion_failed", error=str(exc))


async def _extraction_job(app_state: Any) -> None:
    """Hourly extraction: run lenses over recently ingested payloads."""
    from augur.extraction.executor import LensExecutor
    from augur.extraction.lenses.commodities import COMMODITIES_LENS
    from augur.extraction.tier_a import TierAStore

    pool = app_state.raw_pool
    llm_client = app_state.llm_client

    executor = LensExecutor(llm_client)
    tier_a = TierAStore(pool)

    # Pull payloads ingested in the last 2 hours that have no signals yet
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.payload_id, p.content, p.content_timestamp, p.source_id
            FROM payloads p
            WHERE p.fetched_at > now() - interval '2 hours'
              AND NOT p.rejected
              AND NOT EXISTS (
                SELECT 1 FROM signals s WHERE s.payload_id = p.payload_id
              )
            ORDER BY p.content_timestamp DESC
            LIMIT 200
            """,
        )

    if not rows:
        log.debug("scheduler.extraction_no_payloads")
        return

    log.info("scheduler.extraction_start", n_payloads=len(rows))
    lenses = [COMMODITIES_LENS]
    total_signals = 0

    for row in rows:
        signals = await executor.extract_all_lenses(
            payload_id=row["payload_id"],
            content=row["content"],
            content_timestamp=row["content_timestamp"],
            source_id=row["source_id"],
            lenses=lenses,
        )
        if signals:
            signals = await tier_a.deduplicate_batch(signals)
            stored = await tier_a.store_signals(signals)
            total_signals += stored

    log.info("scheduler.extraction_done", n_signals=total_signals)


# ── Scheduler factory ─────────────────────────────────────────────────────────


def create_scheduler(app_state: Any) -> AsyncIOScheduler:
    """
    Create and configure the APScheduler instance.

    Call start() on the returned scheduler during FastAPI lifespan startup.
    Call shutdown() during shutdown.
    """
    scheduler = AsyncIOScheduler()

    # Ingestion: every hour at :00
    scheduler.add_job(
        func=lambda: asyncio.ensure_future(_ingestion_job(app_state)),
        trigger=IntervalTrigger(hours=1, start_date="2024-01-01 00:00:00"),
        id="ingestion",
        name="Ingestion pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # Extraction: every hour at :10 (offset so ingestion finishes first)
    scheduler.add_job(
        func=lambda: asyncio.ensure_future(_extraction_job(app_state)),
        trigger=IntervalTrigger(hours=1, start_date="2024-01-01 00:10:00"),
        id="extraction",
        name="Extraction pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    return scheduler
