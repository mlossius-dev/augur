"""
Calibration scorer.

Computes per-source and per-lens scores from resolved signal outcomes,
calculates proposed weight updates, and identifies flagged sources/lenses.

Weight update formula (per augur-calibration.md):
    new_weight = 0.5 * tier_baseline
               + 0.3 * prior_weight
               + 0.2 * mean_score_scaled

Where mean_score_scaled maps the source's mean_score from the typical
range (~-0.5 to ~+1.0) into the tier's allowed weight range [0.0, 1.0].
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg
import structlog

from augur.calibration.models import (
    DEFAULT_OUTCOME_SCORES,
    TIER_BASELINES,
    CalibrationReport,
    CalibrationRun,
    LensScore,
    SignalOutcome,
    SourceScore,
)

log = structlog.get_logger(__name__)

# Lenses with mean_score below this threshold are flagged for review
_LENS_WARNING_THRESHOLD = -0.1

# Sources whose proposed weight deviates from prior by more than this are flagged
_SOURCE_FLAG_DELTA = 0.15


async def compute_scores(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    run: CalibrationRun,
) -> tuple[list[SourceScore], list[LensScore]]:
    """
    Compute per-source and per-lens scores for a completed calibration run.

    Reads resolved outcome tracking rows and source registry for tier info.
    """
    async with pool.acquire() as conn:
        # Aggregate outcomes by source
        source_rows = await conn.fetch(
            """
            SELECT source_id,
                   COUNT(*) AS n_signals,
                   COUNT(*) FILTER (WHERE outcome != 'pending') AS n_scored,
                   SUM(score) FILTER (WHERE score IS NOT NULL) AS raw_score,
                   json_object_agg(outcome, cnt) AS breakdown
            FROM (
                SELECT source_id, outcome, score,
                       COUNT(*) OVER (PARTITION BY source_id, outcome) AS cnt
                FROM signal_outcome_tracking
                WHERE run_id = $1 AND outcome != 'pending'
            ) sub
            GROUP BY source_id
            """,
            run_id,
        )

        # Aggregate outcomes by lens
        lens_rows = await conn.fetch(
            """
            SELECT lens_id,
                   COUNT(*) AS n_signals,
                   COUNT(*) FILTER (WHERE outcome != 'pending') AS n_scored,
                   SUM(score) FILTER (WHERE score IS NOT NULL) AS raw_score
            FROM signal_outcome_tracking
            WHERE run_id = $1 AND outcome != 'pending'
            GROUP BY lens_id
            """,
            run_id,
        )

        # Load outcome breakdown per source cleanly
        source_breakdown_rows = await conn.fetch(
            """
            SELECT source_id, outcome, COUNT(*) AS cnt
            FROM signal_outcome_tracking
            WHERE run_id = $1 AND outcome != 'pending'
            GROUP BY source_id, outcome
            """,
            run_id,
        )

        lens_breakdown_rows = await conn.fetch(
            """
            SELECT lens_id, outcome, COUNT(*) AS cnt
            FROM signal_outcome_tracking
            WHERE run_id = $1 AND outcome != 'pending'
            GROUP BY lens_id, outcome
            """,
            run_id,
        )

        # Load source metadata from source registry (for tier + current weight)
        registry_rows = await conn.fetch(
            "SELECT source_id, starting_source_weight FROM payloads "
            "WHERE content_timestamp BETWEEN $1 AND $2 "
            "GROUP BY source_id, starting_source_weight "
            "LIMIT 500",
            run.window_start, run.window_end,
        ) if False else []  # payloads table doesn't store starting_source_weight

    # Build breakdown maps
    source_breakdown: dict[str, dict[str, int]] = {}
    for r in source_breakdown_rows:
        source_breakdown.setdefault(str(r["source_id"]), {})[r["outcome"]] = r["cnt"]

    lens_breakdown: dict[str, dict[str, int]] = {}
    for r in lens_breakdown_rows:
        lens_breakdown.setdefault(str(r["lens_id"]), {})[r["outcome"]] = r["cnt"]

    # Load source registry for tier/weight info
    source_registry = await _load_source_registry()

    source_scores: list[SourceScore] = []
    for r in source_rows:
        sid = str(r["source_id"])
        n_scored = int(r["n_scored"] or 0)
        raw_score = float(r["raw_score"] or 0.0)
        mean_score = raw_score / n_scored if n_scored > 0 else 0.0

        reg = source_registry.get(sid, {})
        tier = str(reg.get("tier", "3"))
        prior_weight = float(reg.get("starting_source_weight", 0.5))
        tier_baseline = TIER_BASELINES.get(tier, 0.5)

        proposed = _compute_new_weight(mean_score, tier_baseline, prior_weight)

        source_scores.append(SourceScore(
            source_id=sid,
            tier=tier,
            n_signals=int(r["n_signals"]),
            n_scored=n_scored,
            raw_score=raw_score,
            mean_score=mean_score,
            prior_weight=prior_weight,
            tier_baseline=tier_baseline,
            proposed_weight=proposed,
            outcome_breakdown=source_breakdown.get(sid, {}),
        ))

    lens_scores: list[LensScore] = []
    for r in lens_rows:
        lid = str(r["lens_id"])
        n_scored = int(r["n_scored"] or 0)
        raw_score = float(r["raw_score"] or 0.0)
        mean_score = raw_score / n_scored if n_scored > 0 else 0.0

        flagged = mean_score < _LENS_WARNING_THRESHOLD
        flag_reason = (
            f"mean_score {mean_score:.3f} below threshold {_LENS_WARNING_THRESHOLD}"
            if flagged else ""
        )

        lens_scores.append(LensScore(
            lens_id=lid,
            n_signals=int(r["n_signals"]),
            n_scored=n_scored,
            raw_score=raw_score,
            mean_score=mean_score,
            outcome_breakdown=lens_breakdown.get(lid, {}),
            flagged=flagged,
            flag_reason=flag_reason,
        ))

    return source_scores, lens_scores


async def build_report(
    pool: asyncpg.Pool,
    *,
    run: CalibrationRun,
) -> CalibrationReport:
    """Build the full CalibrationReport for a completed run."""
    from datetime import datetime, timezone

    source_scores, lens_scores = await compute_scores(pool, run_id=run.run_id, run=run)

    # Count totals
    async with pool.acquire() as conn:
        total_row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE outcome != 'pending') AS scored,
                   COUNT(*) FILTER (WHERE outcome = 'pending') AS pending
            FROM signal_outcome_tracking WHERE run_id = $1
            """,
            run.run_id,
        )

    flagged_sources = [
        s.source_id for s in source_scores
        if abs(s.weight_delta) > _SOURCE_FLAG_DELTA
    ]
    flagged_lenses = [l.lens_id for l in lens_scores if l.flagged]

    return CalibrationReport(
        run_id=run.run_id,
        window_start=run.window_start,
        window_end=run.window_end,
        generated_at=datetime.now(timezone.utc),
        n_signals_total=int(total_row["total"] or 0),
        n_signals_scored=int(total_row["scored"] or 0),
        n_signals_pending=int(total_row["pending"] or 0),
        source_scores=source_scores,
        lens_scores=lens_scores,
        flagged_sources=flagged_sources,
        flagged_lenses=flagged_lenses,
    )


def _compute_new_weight(
    mean_score: float,
    tier_baseline: float,
    prior_weight: float,
) -> float:
    """
    Apply the conservative weight update formula.

        new_weight = 0.5 * tier_baseline
                   + 0.3 * prior_weight
                   + 0.2 * mean_score_scaled

    mean_score is scaled from the typical range [-0.5, 1.0] to [0.0, 1.0]
    by: scaled = (mean_score + 0.5) / 1.5  (clipped to [0, 1])
    """
    scaled = (mean_score + 0.5) / 1.5
    scaled = max(0.0, min(1.0, scaled))

    new_weight = 0.5 * tier_baseline + 0.3 * prior_weight + 0.2 * scaled
    return round(max(0.0, min(1.0, new_weight)), 4)


async def _load_source_registry() -> dict[str, dict[str, Any]]:
    """Load source metadata from the YAML registry (for tier + current weight)."""
    try:
        from augur.ingestion.source_registry import load_sources
        sources = load_sources()
        return {
            s.source_id: {
                "tier": s.tier,
                "starting_source_weight": s.starting_source_weight,
            }
            for s in sources
        }
    except Exception:
        return {}
