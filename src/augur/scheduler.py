"""
APScheduler configuration for the Augur pipeline.

Four recurring jobs:
  - ingestion_job:       hourly at :00 — fetch payloads from all sources
  - extraction_job:      hourly at :10 — extract signals from recent payloads
  - anchoring_job:       hourly at :25 — anchor signals into the causal graph
  - disconfirmation_job: weekly (Sunday 02:00 UTC) — periodic challenge of
                         high-weight edges

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
    """Hourly extraction: run all active lenses over recently ingested payloads."""
    from augur.extraction.executor import LensExecutor, detect_cross_lens_convergence
    from augur.extraction.lenses import ACTIVE_LENSES
    from augur.extraction.tier_a import TierAStore

    pool = app_state.raw_pool
    llm_client = app_state.llm_client

    executor = LensExecutor(llm_client)
    tier_a = TierAStore(pool)

    # Pull payloads ingested in the last 2 hours that have no signals yet
    async with pool.acquire() as conn:
        payload_rows = await conn.fetch(
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

        # Fetch high-weight edges for the inline disconfirmation lens
        disconf_edge_rows = await conn.fetch(
            """
            SELECT e.edge_id,
                   sn.name AS source_name,
                   tn.name AS target_name,
                   e.edge_type,
                   e.current_weight_band AS weight_band,
                   e.falsification_criteria
            FROM edges e
            JOIN nodes sn ON sn.node_id = e.source_node_id
            JOIN nodes tn ON tn.node_id = e.target_node_id
            WHERE e.current_weight_band IN ('strong', 'moderate')
              AND NOT e.deprecated
              AND e.falsification_criteria != ''
            ORDER BY e.updated_at DESC
            LIMIT 30
            """,
        )

    if not payload_rows:
        log.debug("scheduler.extraction_no_payloads")
        return

    edge_context = [dict(r) for r in disconf_edge_rows]
    log.info(
        "scheduler.extraction_start",
        n_payloads=len(payload_rows),
        n_disconf_edges=len(edge_context),
    )
    total_signals = 0

    for row in payload_rows:
        # Run all standard lenses in parallel
        signals = await executor.extract_all_lenses(
            payload_id=row["payload_id"],
            content=row["content"],
            content_timestamp=row["content_timestamp"],
            source_id=row["source_id"],
            lenses=ACTIVE_LENSES,
        )

        # Run inline disconfirmation if we have high-weight edges
        if edge_context:
            disconf_signals = await executor.extract_disconfirmation(
                payload_id=row["payload_id"],
                content=row["content"],
                content_timestamp=row["content_timestamp"],
                source_id=row["source_id"],
                edge_context_rows=edge_context,
            )
            signals.extend(disconf_signals)

        if signals:
            # Log cross-lens convergence (informational; not stored separately yet)
            convergent = detect_cross_lens_convergence(signals)
            if convergent:
                log.info(
                    "scheduler.cross_lens_convergence",
                    payload_id=str(row["payload_id"]),
                    n_convergent_groups=len(convergent),
                )

            signals = await tier_a.deduplicate_batch(signals)
            stored = await tier_a.store_signals(signals)
            total_signals += stored

    log.info("scheduler.extraction_done", n_signals=total_signals)


async def _disconfirmation_job(app_state: Any) -> None:
    """Weekly disconfirmation pass: challenge high-weight edges."""
    from augur.disconfirmation.orchestrator import DisconfirmationOrchestrator

    pool = app_state.raw_pool
    llm_client = app_state.llm_client

    orchestrator = DisconfirmationOrchestrator(pool, llm_client)
    try:
        result = await orchestrator.run_pass(
            limit=20,
            rechallenge_days=7,
            signal_window_days=7,
        )
        log.info(
            "scheduler.disconfirmation_done",
            n_edges=result.n_edges_challenged,
            n_found=result.n_found,
            n_not_found=result.n_not_found,
            n_applied=result.n_operations_applied,
        )
    except Exception as exc:
        log.error("scheduler.disconfirmation_failed", error=str(exc))


async def _anchoring_job(app_state: Any) -> None:
    """Hourly anchoring: convert unanchored signals into graph mutations."""
    from augur.anchoring.orchestrator import AnchoringOrchestrator

    pool = app_state.raw_pool
    llm_client = app_state.llm_client

    orchestrator = AnchoringOrchestrator(pool, llm_client)
    try:
        result = await orchestrator.run_cycle(min_age_hours=1, limit=200)
        log.info(
            "scheduler.anchoring_done",
            n_batches=result.n_batches,
            n_signals=result.n_signals_processed,
            n_applied=result.n_applied,
            n_rejected=result.n_rejected,
        )
    except Exception as exc:
        log.error("scheduler.anchoring_failed", error=str(exc))


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

    # Anchoring: every hour at :25 (after extraction has had 15 min to run)
    scheduler.add_job(
        func=lambda: asyncio.ensure_future(_anchoring_job(app_state)),
        trigger=IntervalTrigger(hours=1, start_date="2024-01-01 00:25:00"),
        id="anchoring",
        name="Anchoring pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    # Disconfirmation: weekly, Sunday 02:00 UTC
    from apscheduler.triggers.cron import CronTrigger
    scheduler.add_job(
        func=lambda: asyncio.ensure_future(_disconfirmation_job(app_state)),
        trigger=CronTrigger(day_of_week="sun", hour=2, minute=0, timezone="UTC"),
        id="disconfirmation",
        name="Disconfirmation pass",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    return scheduler
