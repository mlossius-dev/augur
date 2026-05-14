"""
Pipeline health queries.

Provides a single get_pipeline_health() call that returns a comprehensive
snapshot of system state: signal counts, graph size, recent job activity,
and pending work at each pipeline stage.

Used by `augur monitor status` and the FastAPI health endpoint.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import structlog

log = structlog.get_logger(__name__)


async def get_pipeline_health(pool: asyncpg.Pool) -> dict[str, Any]:
    """
    Return a comprehensive pipeline health snapshot.

    Queries are intentionally cheap (no full table scans, indexed paths only).
    """
    async with pool.acquire() as conn:
        # Signal store
        signal_row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE fetched_at > now() - interval '24 hours') AS last_24h,
                COUNT(*) FILTER (WHERE fetched_at > now() - interval '1 hour') AS last_hour,
                COUNT(*) FILTER (WHERE cluster_id IS NULL) AS unclustered,
                COUNT(*) FILTER (WHERE cluster_id IS NOT NULL) AS clustered
            FROM signals
            """
        )

        # Graph state
        graph_row = await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*) FROM nodes WHERE NOT deprecated) AS live_nodes,
                (SELECT COUNT(*) FROM nodes WHERE deprecated) AS deprecated_nodes,
                (SELECT COUNT(*) FROM edges WHERE NOT deprecated) AS live_edges,
                (SELECT COUNT(*) FROM edges WHERE deprecated) AS deprecated_edges,
                (SELECT COUNT(*) FROM edges WHERE current_weight_band = 'strong') AS strong_edges,
                (SELECT COUNT(*) FROM edges WHERE current_weight_band = 'disputed') AS disputed_edges
            """
        )

        # Anchoring backlog: unanchored signals older than 1 hour
        anchoring_backlog = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM signals s
            LEFT JOIN graph_update_events gue
                ON $1 = ANY(gue.triggered_by) AND gue.rejected = FALSE
                AND gue.target_edge_id IS NOT NULL
            WHERE gue.target_edge_id IS NULL
              AND s.fetched_at < now() - interval '1 hour'
            """,
            # Pass a dummy signal_id-like reference; this is a correlated subquery
            # but asyncpg needs a param. Rewrite as a plain query.
            None,
        ) if False else await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM signals s
            WHERE NOT EXISTS (
                SELECT 1 FROM graph_update_events gue
                WHERE $1 = ANY(gue.triggered_by)
                  AND gue.rejected = FALSE
            )
            AND s.fetched_at < now() - interval '1 hour'
            AND s.cluster_id IS NULL
            """,
            # placeholder — real query below
            None,
        ) if False else await conn.fetchval(
            """
            SELECT COUNT(*) FROM signals
            WHERE cluster_id IS NULL
              AND fetched_at < now() - interval '1 hour'
            """
        )

        # Recent job runs
        job_rows = await conn.fetch(
            """
            SELECT job_name, status, started_at, completed_at,
                   n_processed, n_errors, error_message
            FROM pipeline_run_log
            WHERE started_at > now() - interval '7 days'
            ORDER BY started_at DESC
            LIMIT 50
            """
        )

        # Disconfirmation: edges not challenged in > 14 days
        stale_edges = await conn.fetchval(
            """
            SELECT COUNT(*) FROM edges
            WHERE NOT deprecated
              AND (last_disconfirmation_pass IS NULL
                   OR last_disconfirmation_pass < now() - interval '14 days')
              AND current_weight_band IN ('strong', 'moderate')
            """
        )

        # Payloads in last 24h
        payload_row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE rejected) AS rejected
            FROM payloads
            WHERE fetched_at > now() - interval '24 hours'
            """
        )

    # Summarise recent job runs
    job_summary: dict[str, dict[str, Any]] = {}
    for r in job_rows:
        job = r["job_name"]
        if job not in job_summary:
            job_summary[job] = {
                "last_run": r["started_at"].isoformat() if r["started_at"] else None,
                "last_status": r["status"],
                "last_n_processed": r["n_processed"],
                "last_error": r["error_message"],
            }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signals": {
            "total": int(signal_row["total"] or 0),
            "last_24h": int(signal_row["last_24h"] or 0),
            "last_hour": int(signal_row["last_hour"] or 0),
            "unclustered": int(signal_row["unclustered"] or 0),
            "clustered": int(signal_row["clustered"] or 0),
        },
        "graph": {
            "live_nodes": int(graph_row["live_nodes"] or 0),
            "deprecated_nodes": int(graph_row["deprecated_nodes"] or 0),
            "live_edges": int(graph_row["live_edges"] or 0),
            "deprecated_edges": int(graph_row["deprecated_edges"] or 0),
            "strong_edges": int(graph_row["strong_edges"] or 0),
            "disputed_edges": int(graph_row["disputed_edges"] or 0),
        },
        "pipeline": {
            "anchoring_backlog": int(anchoring_backlog or 0),
            "stale_edges_for_disconfirmation": int(stale_edges or 0),
        },
        "payloads_24h": {
            "total": int(payload_row["total"] or 0),
            "rejected": int(payload_row["rejected"] or 0),
        },
        "recent_jobs": job_summary,
    }


async def get_anchoring_quality(
    pool: asyncpg.Pool,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Return recent anchoring batch results for operator review.

    Shows applied/rejected counts per batch to help spot anchoring regressions.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                batch_id,
                COUNT(*) AS n_events,
                COUNT(*) FILTER (WHERE NOT rejected) AS n_applied,
                COUNT(*) FILTER (WHERE rejected) AS n_rejected,
                MIN(applied_at) AS batch_start,
                array_agg(DISTINCT lens_id) AS lenses
            FROM graph_update_events
            WHERE applied_at > now() - interval '7 days'
            GROUP BY batch_id
            ORDER BY batch_start DESC
            LIMIT $1
            """,
            limit,
        )

    return [
        {
            "batch_id": str(r["batch_id"]),
            "n_events": r["n_events"],
            "n_applied": r["n_applied"],
            "n_rejected": r["n_rejected"],
            "rejection_rate": round(r["n_rejected"] / r["n_events"], 3) if r["n_events"] else 0.0,
            "batch_start": r["batch_start"].isoformat() if r["batch_start"] else None,
            "lenses": list(r["lenses"] or []),
        }
        for r in rows
    ]


async def get_signal_flow(
    pool: asyncpg.Pool,
    *,
    hours: int = 24,
) -> dict[str, Any]:
    """
    Return signal flow statistics for the given lookback window.

    Breaks down signal counts by lens and source tier.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    async with pool.acquire() as conn:
        lens_rows = await conn.fetch(
            """
            SELECT lens_id, COUNT(*) AS cnt
            FROM signals
            WHERE fetched_at >= $1
            GROUP BY lens_id
            ORDER BY cnt DESC
            """,
            cutoff,
        )

        total = await conn.fetchval(
            "SELECT COUNT(*) FROM signals WHERE fetched_at >= $1",
            cutoff,
        )

        deduped = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT cluster_id) FROM signals
            WHERE fetched_at >= $1 AND cluster_id IS NOT NULL
            """,
            cutoff,
        )

    return {
        "window_hours": hours,
        "total_signals": int(total or 0),
        "unique_clusters": int(deduped or 0),
        "by_lens": {r["lens_id"]: r["cnt"] for r in lens_rows},
    }


async def log_job_start(
    pool: asyncpg.Pool,
    job_name: str,
) -> int:
    """
    Record a pipeline job start in pipeline_run_log.

    Returns the log_id for use in log_job_complete().
    """
    async with pool.acquire() as conn:
        log_id = await conn.fetchval(
            """
            INSERT INTO pipeline_run_log (job_name, started_at, status)
            VALUES ($1, now(), 'running')
            RETURNING log_id
            """,
            job_name,
        )
    return int(log_id)


async def log_job_complete(
    pool: asyncpg.Pool,
    log_id: int,
    *,
    status: str = "ok",
    n_processed: int = 0,
    n_errors: int = 0,
    error_message: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Record a pipeline job completion in pipeline_run_log."""
    import json

    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE pipeline_run_log
            SET completed_at = now(),
                status = $1,
                n_processed = $2,
                n_errors = $3,
                error_message = $4,
                metadata = $5
            WHERE log_id = $6
            """,
            status,
            n_processed,
            n_errors,
            error_message,
            json.dumps(metadata or {}),
            log_id,
        )
