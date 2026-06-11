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
    from augur.monitoring.health import log_job_start, log_job_complete

    settings = get_settings()
    pool = app_state.raw_pool

    pipeline = IngestionPipeline(
        pool,
        archive_root=settings.payload_archive_root,
        searxng_url=str(settings.searxng_url) if settings.searxng_url else "",
    )

    log_id = await log_job_start(pool, "ingestion")
    try:
        summary = await pipeline.run_all()
        n = sum(v for v in summary.values() if isinstance(v, int))
        await log_job_complete(pool, log_id, n_processed=n, metadata=summary)
        log.info("scheduler.ingestion_done", summary=summary)
    except Exception as exc:
        await log_job_complete(pool, log_id, status="error", error_message=str(exc))
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

            # Register signals into the live calibration run for ongoing tracking
            try:
                from augur.calibration.live_tracker import register_live_signals
                await register_live_signals(pool, signals)
            except Exception as exc:
                log.warning("scheduler.live_calibration_register_failed", error=str(exc))

    log.info("scheduler.extraction_done", n_signals=total_signals)


async def _disconfirmation_job(app_state: Any) -> None:
    """Weekly disconfirmation pass: challenge high-weight edges."""
    from augur.disconfirmation.orchestrator import DisconfirmationOrchestrator
    from augur.monitoring.health import log_job_start, log_job_complete

    pool = app_state.raw_pool
    llm_client = app_state.llm_client

    orchestrator = DisconfirmationOrchestrator(pool, llm_client)
    log_id = await log_job_start(pool, "disconfirmation")
    try:
        result = await orchestrator.run_pass(
            limit=20,
            rechallenge_days=7,
            signal_window_days=7,
        )
        await log_job_complete(
            pool, log_id,
            n_processed=result.n_edges_challenged,
            metadata={"n_found": result.n_found, "n_not_found": result.n_not_found},
        )
        log.info(
            "scheduler.disconfirmation_done",
            n_edges=result.n_edges_challenged,
            n_found=result.n_found,
            n_not_found=result.n_not_found,
            n_applied=result.n_operations_applied,
        )
    except Exception as exc:
        await log_job_complete(pool, log_id, status="error", error_message=str(exc))
        log.error("scheduler.disconfirmation_failed", error=str(exc))


async def _anchoring_job(app_state: Any) -> None:
    """Hourly anchoring: convert unanchored signals into graph mutations."""
    from augur.anchoring.orchestrator import AnchoringOrchestrator
    from augur.monitoring.health import log_job_start, log_job_complete

    pool = app_state.raw_pool
    llm_client = app_state.llm_client

    orchestrator = AnchoringOrchestrator(pool, llm_client)
    log_id = await log_job_start(pool, "anchoring")
    try:
        result = await orchestrator.run_cycle(min_age_hours=1, limit=200)
        await log_job_complete(
            pool, log_id,
            n_processed=result.n_signals_processed,
            metadata={"n_batches": result.n_batches, "n_applied": result.n_applied, "n_rejected": result.n_rejected},
        )
        log.info(
            "scheduler.anchoring_done",
            n_batches=result.n_batches,
            n_signals=result.n_signals_processed,
            n_applied=result.n_applied,
            n_rejected=result.n_rejected,
        )
    except Exception as exc:
        await log_job_complete(pool, log_id, status="error", error_message=str(exc))
        log.error("scheduler.anchoring_failed", error=str(exc))


async def _dimension_notes_job(app_state: Any) -> None:
    """Hourly: refresh per-dimension editorial notes (free-tier model)."""
    from augur.presentation.notes import regenerate_dimension_notes

    pool = app_state.raw_pool
    llm = app_state.llm_client
    try:
        summary = await regenerate_dimension_notes(pool, llm)
        log.info("scheduler.dimension_notes_done", summary=summary)
    except Exception as exc:
        log.error("scheduler.dimension_notes_failed", error=str(exc))


async def _prune_sessions_job(app_state: Any) -> None:
    """Weekly session pruning: remove stale conversation sessions."""
    from augur.conversation.session import prune_sessions

    pool = app_state.raw_pool
    try:
        deleted = await prune_sessions(pool, max_age_hours=48)
        log.info("scheduler.prune_sessions_done", deleted=deleted)
    except Exception as exc:
        log.error("scheduler.prune_sessions_failed", error=str(exc))


async def _projection_job(app_state: Any) -> None:
    """Weekly scenario projection: generate scenarios for all five dimensions."""
    from augur.monitoring.health import log_job_complete, log_job_start
    from augur.projection.orchestrator import ProjectionOrchestrator

    pool = app_state.raw_pool
    llm = app_state.llm_client
    log_id = await log_job_start(pool, "scenario_projection")
    try:
        orch = ProjectionOrchestrator(pool, llm)
        results = await orch.run_all_dimensions()
        n_scenarios = sum(len(r.scenarios) for r in results)
        await log_job_complete(pool, log_id, n_processed=n_scenarios,
                               metadata={"dimensions": len(results)})
        log.info("scheduler.projection_done", n_scenarios=n_scenarios)
    except Exception as exc:
        await log_job_complete(pool, log_id, status="error", error_message=str(exc))
        log.error("scheduler.projection_failed", error=str(exc))


async def _live_calibration_checkpoint_job(app_state: Any) -> None:
    """Weekly live calibration checkpoint: resolve pending signal outcomes."""
    from augur.calibration.live_tracker import checkpoint_live_outcomes
    from augur.monitoring.health import log_job_start, log_job_complete

    pool = app_state.raw_pool
    log_id = await log_job_start(pool, "live_calibration_checkpoint")
    try:
        summary = await checkpoint_live_outcomes(pool)
        n_resolved = sum(summary.values())
        await log_job_complete(pool, log_id, n_processed=n_resolved, metadata=summary)
        log.info("scheduler.live_calibration_checkpoint_done", summary=summary)
    except Exception as exc:
        await log_job_complete(pool, log_id, status="error", error_message=str(exc))
        log.error("scheduler.live_calibration_checkpoint_failed", error=str(exc))


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

    # Dimension notes: every hour at :40 (after anchoring has settled)
    scheduler.add_job(
        func=lambda: asyncio.ensure_future(_dimension_notes_job(app_state)),
        trigger=IntervalTrigger(hours=1, start_date="2024-01-01 00:40:00"),
        id="dimension_notes",
        name="Dimension editorial notes",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )

    from apscheduler.triggers.cron import CronTrigger

    # Disconfirmation: weekly, Sunday 02:00 UTC
    scheduler.add_job(
        func=lambda: asyncio.ensure_future(_disconfirmation_job(app_state)),
        trigger=CronTrigger(day_of_week="sun", hour=2, minute=0, timezone="UTC"),
        id="disconfirmation",
        name="Disconfirmation pass",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # Live calibration checkpoint: weekly, Sunday 04:00 UTC (after disconfirmation)
    scheduler.add_job(
        func=lambda: asyncio.ensure_future(_live_calibration_checkpoint_job(app_state)),
        trigger=CronTrigger(day_of_week="sun", hour=4, minute=0, timezone="UTC"),
        id="live_calibration_checkpoint",
        name="Live calibration checkpoint",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # Scenario projection: weekly, Sunday 06:00 UTC (after calibration checkpoint)
    scheduler.add_job(
        func=lambda: asyncio.ensure_future(_projection_job(app_state)),
        trigger=CronTrigger(day_of_week="sun", hour=6, minute=0, timezone="UTC"),
        id="scenario_projection",
        name="Scenario projection",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # Session pruning: weekly, Sunday 07:00 UTC
    scheduler.add_job(
        func=lambda: asyncio.ensure_future(_prune_sessions_job(app_state)),
        trigger=CronTrigger(day_of_week="sun", hour=7, minute=0, timezone="UTC"),
        id="prune_sessions",
        name="Prune stale conversation sessions",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    return scheduler
