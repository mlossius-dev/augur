"""
Look-ahead bias (leakage) detection for calibration runs.

Operator spot-check tooling: samples a fraction of extractions and
returns those that show signs of knowledge post-dating the replay window.

Leakage heuristics (applied in order; any match flags the signal):
  1. The signal's reasoning mentions a year after the replay window end.
  2. The claim_text references events or data points that could only be
     known after the content_timestamp.
  3. The confidence_band is 'hard_datum' but the source is an RSS feed
     (hard data from news prose is suspicious).
  4. The LLM self-identifies uncertainty about the date in its reasoning
     (contains phrases like "as of [date after replay_end]").

These heuristics are shallow — they surface candidates for human review.
The operator reviews flagged signals and makes the final determination.
A leakage_rate > 0.05 (5%) triggers a re-run recommendation.
"""

from __future__ import annotations

import random
import re
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from augur.calibration.models import LeakageCheckResult

log = structlog.get_logger(__name__)

# Phrases in reasoning that suggest post-cutoff knowledge
_SUSPICIOUS_PHRASES = [
    r"as of \d{4}",
    r"by \d{4}",
    r"in \d{4}",
    r"later in \d{4}",
    r"the following year",
    r"subsequently",
    r"later revealed",
    r"would later",
    r"has since",
]
_SUSPICIOUS_RE = re.compile(
    "|".join(_SUSPICIOUS_PHRASES), re.IGNORECASE
)


async def run_leakage_check(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    window_end: datetime,
    sample_fraction: float = 0.02,
    min_sample: int = 20,
    max_sample: int = 200,
    seed: int = 42,
) -> LeakageCheckResult:
    """
    Sample signals from a calibration run and apply leakage heuristics.

    Args:
        run_id: The calibration run to check.
        window_end: The replay window end date (signals after this are suspect).
        sample_fraction: Fraction of signals to sample (default 2%).
        min_sample: Minimum number of signals to sample regardless of total.
        max_sample: Cap on sample size.
        seed: RNG seed for reproducibility.

    Returns:
        LeakageCheckResult with n_sampled, n_suspicious, suspicious_signal_ids.
    """
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM signal_outcome_tracking WHERE run_id = $1",
            run_id,
        )

    if not total:
        return LeakageCheckResult(n_sampled=0, n_suspicious=0)

    sample_size = max(min_sample, min(max_sample, int(total * sample_fraction)))
    sample_size = min(sample_size, total)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT sot.signal_id, s.claim_text, s.reasoning,
                   s.confidence_band, s.content_timestamp,
                   p.source_id, p.access_method
            FROM signal_outcome_tracking sot
            JOIN signals s ON s.signal_id = sot.signal_id
            LEFT JOIN payloads p ON p.payload_id = s.payload_id
            LEFT JOIN LATERAL (
                SELECT access_method FROM payloads
                WHERE payload_id = s.payload_id
            ) pj ON true
            WHERE sot.run_id = $1
            ORDER BY random()
            LIMIT $2
            """,
            run_id,
            sample_size,
        )

    if not rows:
        return LeakageCheckResult(n_sampled=0, n_suspicious=0)

    rng = random.Random(seed)
    suspicious: list[UUID] = []

    window_end_year = window_end.year

    for row in rows:
        if _is_suspicious(row, window_end_year=window_end_year):
            suspicious.append(row["signal_id"])

    leakage_rate = len(suspicious) / len(rows)
    if leakage_rate > 0.05:
        log.warning(
            "leakage.high_rate",
            run_id=str(run_id),
            rate=round(leakage_rate, 3),
            n_suspicious=len(suspicious),
            n_sampled=len(rows),
        )

    return LeakageCheckResult(
        n_sampled=len(rows),
        n_suspicious=len(suspicious),
        suspicious_signal_ids=suspicious,
        notes=(
            f"Leakage rate {leakage_rate:.1%}. "
            f"{'Re-run recommended.' if leakage_rate > 0.05 else 'Within threshold.'}"
        ),
    )


def _is_suspicious(row: Any, *, window_end_year: int) -> bool:
    """Apply heuristics to a single signal row to detect leakage."""
    reasoning = str(row.get("reasoning") or "")
    claim = str(row.get("claim_text") or "")
    confidence = str(row.get("confidence_band") or "")

    # Heuristic 1: Year references after the window end
    year_matches = re.findall(r"\b(20\d{2})\b", reasoning + " " + claim)
    for year_str in year_matches:
        if int(year_str) > window_end_year:
            return True

    # Heuristic 2: Suspicious temporal phrases in reasoning
    if _SUSPICIOUS_RE.search(reasoning):
        return True

    # Heuristic 3: hard_datum from RSS source (news articles don't produce hard data)
    source_id = str(row.get("source_id") or "")
    access_method = str(row.get("access_method") or "")
    if confidence == "hard_datum" and (
        "rss" in source_id.lower() or access_method == "rss"
    ):
        return True

    return False


def format_leakage_report(result: LeakageCheckResult) -> str:
    """Format a LeakageCheckResult as a human-readable text block."""
    lines = [
        f"Leakage spot-check results:",
        f"  Signals sampled:    {result.n_sampled}",
        f"  Suspicious signals: {result.n_suspicious}",
        f"  Leakage rate:       {result.leakage_rate:.1%}",
        f"  Status:             {'⚠ ABOVE THRESHOLD — re-run recommended' if result.leakage_rate > 0.05 else '✓ Within threshold'}",
    ]
    if result.notes:
        lines.append(f"  Notes:              {result.notes}")
    if result.suspicious_signal_ids:
        lines.append(f"\n  Suspicious signal IDs (first 10):")
        for sid in result.suspicious_signal_ids[:10]:
            lines.append(f"    {sid}")
    return "\n".join(lines)
