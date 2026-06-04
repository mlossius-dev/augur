"""
Live calibration tracker.

During live operation (Phase 7+), signals are registered into a continuously-open
CalibrationRun (the "live run") as they are extracted. Periodically (weekly by
default), a checkpoint resolves pending outcomes on the live run, accumulating
a growing dataset of signal-survival evidence that operators can query.

Unlike replay runs, the live run:
  - Never has a fixed window_end (it's always advancing).
  - Never enters COMPLETE status (it stays RUNNING indefinitely).
  - Is the background source of truth for ongoing source quality.
  - Can be used to trigger recalibration without a full replay run.

One live run is active at a time. The run_id is stored in the
live_calibration_config singleton row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from augur.calibration.models import CalibrationRun, CalibrationStatus
from augur.calibration.tracker import register_signals_for_run, resolve_outcomes

log = structlog.get_logger(__name__)

# How far back the live run's observation window extends for outcome resolution
_DEFAULT_OBSERVATION_DAYS = 90


async def ensure_live_run(pool: asyncpg.Pool) -> CalibrationRun:
    """
    Get or create the active live calibration run.

    If a live run already exists in live_calibration_config, return it.
    Otherwise, create a new CalibrationRun (status=running) starting from
    90 days ago and store its ID.

    Idempotent: safe to call on every scheduler tick.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT run_id FROM live_calibration_config WHERE singleton = TRUE"
        )

    if row:
        run_id: UUID = row["run_id"]
        return await _load_run(pool, run_id)

    # Create a new live run
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(days=_DEFAULT_OBSERVATION_DAYS)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO calibration_runs
                    (run_id, window_start, window_end,
                     observation_extension_days, status, created_at, notes)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                run_id,
                window_start,
                now,
                _DEFAULT_OBSERVATION_DAYS,
                CalibrationStatus.RUNNING.value,
                now,
                "live-operation-tracking",
            )
            await conn.execute(
                """
                INSERT INTO live_calibration_config (singleton, run_id, created_at)
                VALUES (TRUE, $1, $2)
                ON CONFLICT (singleton) DO UPDATE SET run_id = $1, created_at = $2
                """,
                run_id,
                now,
            )

    log.info("live_tracker.run_created", run_id=str(run_id))

    return CalibrationRun(
        run_id=run_id,
        window_start=window_start,
        window_end=now,
        observation_extension_days=_DEFAULT_OBSERVATION_DAYS,
        status=CalibrationStatus.RUNNING,
        created_at=now,
        notes="live-operation-tracking",
    )


async def register_live_signals(
    pool: asyncpg.Pool,
    signals: list[dict[str, Any]],
) -> int:
    """
    Register freshly-extracted signals into the active live calibration run.

    Call this after every extraction batch during live operation.
    Returns the number of signals registered.
    """
    if not signals:
        return 0

    run = await ensure_live_run(pool)
    return await register_signals_for_run(pool, run_id=run.run_id, signals=signals)


async def checkpoint_live_outcomes(
    pool: asyncpg.Pool,
    *,
    batch_size: int = 2000,
) -> dict[str, int]:
    """
    Resolve pending outcomes on the live calibration run.

    Signals are eligible once they are older than observation_extension_days.
    This is called weekly by the scheduler.

    Returns a summary dict {outcome_name: count}.
    """
    run = await ensure_live_run(pool)

    # Observation cutoff: signals at least 90 days old can be scored
    observation_cutoff = datetime.now(timezone.utc) - timedelta(
        days=run.observation_extension_days
    )

    log.info(
        "live_tracker.checkpoint_start",
        run_id=str(run.run_id),
        observation_cutoff=observation_cutoff.isoformat(),
    )

    summary = await resolve_outcomes(
        pool,
        run_id=run.run_id,
        observation_cutoff=observation_cutoff,
        batch_size=batch_size,
    )

    log.info(
        "live_tracker.checkpoint_done",
        run_id=str(run.run_id),
        summary=summary,
    )
    return summary


async def live_run_stats(pool: asyncpg.Pool) -> dict[str, Any]:
    """
    Return summary statistics for the active live calibration run.

    Used by the monitoring CLI.
    """
    run = await ensure_live_run(pool)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE outcome != 'pending') AS scored,
                COUNT(*) FILTER (WHERE outcome = 'pending') AS pending,
                MIN(content_timestamp) AS oldest,
                MAX(content_timestamp) AS newest
            FROM signal_outcome_tracking
            WHERE run_id = $1
            """,
            run.run_id,
        )

        outcome_breakdown = await conn.fetch(
            """
            SELECT outcome, COUNT(*) AS cnt
            FROM signal_outcome_tracking
            WHERE run_id = $1 AND outcome != 'pending'
            GROUP BY outcome
            ORDER BY cnt DESC
            """,
            run.run_id,
        )

    breakdown = {r["outcome"]: r["cnt"] for r in outcome_breakdown}

    return {
        "run_id": str(run.run_id),
        "n_total": int(row["total"] or 0),
        "n_scored": int(row["scored"] or 0),
        "n_pending": int(row["pending"] or 0),
        "oldest_signal": row["oldest"].isoformat() if row["oldest"] else None,
        "newest_signal": row["newest"].isoformat() if row["newest"] else None,
        "outcome_breakdown": breakdown,
    }


async def _load_run(pool: asyncpg.Pool, run_id: UUID) -> CalibrationRun:
    """Load a CalibrationRun from the DB by ID."""
    import json

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM calibration_runs WHERE run_id = $1", run_id
        )

    if row is None:
        raise ValueError(f"Live calibration run {run_id} not found in DB")

    summary = row["summary"]
    if isinstance(summary, str):
        summary = json.loads(summary)

    return CalibrationRun(
        run_id=row["run_id"],
        window_start=row["window_start"],
        window_end=row["window_end"],
        observation_extension_days=row["observation_extension_days"],
        source_subset=list(row["source_subset"]) if row["source_subset"] else None,
        lens_subset=list(row["lens_subset"]) if row["lens_subset"] else None,
        model_overrides=json.loads(row["model_overrides"]) if isinstance(row["model_overrides"], str) else dict(row["model_overrides"] or {}),
        status=CalibrationStatus(row["status"]),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        notes=row["notes"] or "",
        summary=summary,
    )
