"""
Signal outcome tracker for calibration runs.

Responsibilities:
  1. register_signals_for_run(): insert pending tracking rows when signals
     are extracted during replay.
  2. resolve_outcomes(): walk pending signals and determine their outcome
     based on what happened to them in the graph (anchored/clustered/isolated).
  3. outcome_for_signal(): determine a single signal's outcome by inspecting
     graph_update_events and the signals table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from augur.calibration.models import (
    DEFAULT_OUTCOME_SCORES,
    SignalOutcome,
    SignalOutcomeRecord,
)

log = structlog.get_logger(__name__)


async def register_signals_for_run(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    signals: list[dict[str, Any]],
) -> int:
    """
    Insert pending outcome-tracking rows for a batch of extracted signals.

    Returns number of rows inserted (skips duplicates for the same run/signal).
    """
    if not signals:
        return 0

    inserted = 0
    async with pool.acquire() as conn:
        for sig in signals:
            try:
                await conn.execute(
                    """
                    INSERT INTO signal_outcome_tracking
                        (run_id, signal_id, source_id, lens_id,
                         content_timestamp, outcome)
                    VALUES ($1, $2, $3, $4, $5, 'pending')
                    ON CONFLICT (run_id, signal_id) DO NOTHING
                    """,
                    run_id,
                    sig["signal_id"],
                    sig.get("source_id", ""),
                    sig["lens_id"],
                    sig["content_timestamp"],
                )
                inserted += 1
            except Exception as exc:
                log.warning(
                    "tracker.register_failed",
                    signal_id=str(sig.get("signal_id")),
                    error=str(exc),
                )

    return inserted


async def resolve_outcomes(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    observation_cutoff: datetime,
    batch_size: int = 500,
) -> dict[str, int]:
    """
    Resolve pending signal outcomes for a calibration run.

    Processes signals whose content_timestamp is before observation_cutoff
    (i.e., enough time has passed for outcomes to materialise).

    Returns a summary: {outcome_name: count}.
    """
    summary: dict[str, int] = {}

    async with pool.acquire() as conn:
        # Fetch pending signals eligible for scoring
        pending = await conn.fetch(
            """
            SELECT tracking_id, signal_id, source_id, lens_id, content_timestamp
            FROM signal_outcome_tracking
            WHERE run_id = $1
              AND outcome = 'pending'
              AND content_timestamp < $2
            ORDER BY content_timestamp ASC
            LIMIT $3
            """,
            run_id,
            observation_cutoff,
            batch_size,
        )

        if not pending:
            return {}

        log.info(
            "tracker.resolving",
            run_id=str(run_id),
            n_pending=len(pending),
        )

        for row in pending:
            outcome = await outcome_for_signal(conn, signal_id=row["signal_id"])
            score = DEFAULT_OUTCOME_SCORES.get(outcome, 0.0)

            await conn.execute(
                """
                UPDATE signal_outcome_tracking
                SET outcome = $1, score = $2, resolved_at = now()
                WHERE tracking_id = $3
                """,
                outcome.value,
                score,
                row["tracking_id"],
            )

            summary[outcome.value] = summary.get(outcome.value, 0) + 1

    log.info(
        "tracker.resolved",
        run_id=str(run_id),
        summary=summary,
    )
    return summary


async def outcome_for_signal(
    conn: asyncpg.Connection,
    *,
    signal_id: UUID,
) -> SignalOutcome:
    """
    Determine a single signal's calibration outcome by inspecting the DB.

    Decision tree:
      1. Was the signal anchored (appears in graph_update_events.triggered_by)?
         a. Is the edge it contributed to deprecated?   → ANCHORED_DEPRECATED
         b. Was the edge strengthened (weight history has strengthen event)?
                                                        → ANCHORED_STRENGTHENED
         c. Was the edge weakened (weight history has weaken event)?
                                                        → ANCHORED_WEAKENED
         d. Otherwise:                                  → ANCHORED_PERSISTENT
      2. Did the signal cluster in Tier A (cluster_id IS NOT NULL)?
                                                        → CLUSTERED_BUT_NOT_ANCHORED
      3. Was the signal present but never clustered or anchored?
                                                        → ISOLATED_IN_TIER_A
    """
    # Step 1: check if anchored (signal_id appears in any graph_update_event)
    anchored_row = await conn.fetchrow(
        """
        SELECT gue.target_edge_id, e.deprecated,
               e.current_weight_band
        FROM graph_update_events gue
        LEFT JOIN edges e ON e.edge_id = gue.target_edge_id
        WHERE $1 = ANY(gue.triggered_by)
          AND gue.rejected = FALSE
          AND gue.target_edge_id IS NOT NULL
        ORDER BY gue.applied_at DESC
        LIMIT 1
        """,
        signal_id,
    )

    if anchored_row:
        edge_id = anchored_row["target_edge_id"]
        deprecated = anchored_row["deprecated"]

        if deprecated:
            return SignalOutcome.ANCHORED_DEPRECATED

        # Check weight history for strengthen/weaken events after anchoring
        weight_changes = await conn.fetch(
            """
            SELECT change_type FROM edge_weight_history
            WHERE edge_id = $1
            ORDER BY content_timestamp DESC
            LIMIT 5
            """,
            edge_id,
        )
        change_types = [r["change_type"] for r in weight_changes]
        if "strengthened" in change_types:
            return SignalOutcome.ANCHORED_STRENGTHENED
        if "weakened" in change_types or "disconfirmation" in change_types:
            return SignalOutcome.ANCHORED_WEAKENED
        return SignalOutcome.ANCHORED_PERSISTENT

    # Step 2: check if clustered
    sig_row = await conn.fetchrow(
        "SELECT cluster_id FROM signals WHERE signal_id = $1",
        signal_id,
    )
    if sig_row is None:
        # Signal not found — was likely rejected upstream
        return SignalOutcome.EXTRACTION_REJECTED

    if sig_row["cluster_id"] is not None:
        return SignalOutcome.CLUSTERED_BUT_NOT_ANCHORED

    return SignalOutcome.ISOLATED_IN_TIER_A
