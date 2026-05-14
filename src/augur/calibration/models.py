"""
Calibration data models.

CalibrationRun     : configuration and status for one replay-mode run.
SignalOutcome      : the seven possible signal outcomes with their score weights.
SignalOutcomeRecord: one resolved signal in a calibration run.
SourceScore        : per-source aggregated score for a completed run.
LensScore          : per-lens aggregated score for a completed run.
CalibrationReport  : full report produced at run completion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class CalibrationStatus(StrEnum):
    CONFIGURED = "configured"
    RUNNING = "running"
    SCORING = "scoring"
    COMPLETE = "complete"
    FAILED = "failed"


class SignalOutcome(StrEnum):
    """
    Possible outcomes for a signal tracked through calibration.

    Scores are the initial configuration values per augur-calibration.md.
    They can be tuned per-run via the calibration_run.model_overrides config.
    """

    ANCHORED_STRENGTHENED = "anchored_strengthened"
    ANCHORED_PERSISTENT = "anchored_persistent"
    ANCHORED_WEAKENED = "anchored_weakened"
    ANCHORED_DEPRECATED = "anchored_deprecated"
    CLUSTERED_BUT_NOT_ANCHORED = "clustered_but_not_anchored"
    ISOLATED_IN_TIER_A = "isolated_in_tier_a"
    EXTRACTION_REJECTED = "extraction_rejected"
    PENDING = "pending"


# Default score weights per augur-calibration.md
DEFAULT_OUTCOME_SCORES: dict[SignalOutcome, float] = {
    SignalOutcome.ANCHORED_STRENGTHENED: 1.0,
    SignalOutcome.ANCHORED_PERSISTENT: 0.3,
    SignalOutcome.ANCHORED_WEAKENED: -0.3,
    SignalOutcome.ANCHORED_DEPRECATED: -1.0,
    SignalOutcome.CLUSTERED_BUT_NOT_ANCHORED: 0.0,
    SignalOutcome.ISOLATED_IN_TIER_A: -0.2,
    SignalOutcome.EXTRACTION_REJECTED: 0.0,
    SignalOutcome.PENDING: 0.0,
}

# Tier baseline weights (from sources.yaml starting_source_weight tiers)
TIER_BASELINES: dict[str, float] = {
    "structured_data": 0.90,
    "1": 0.80,
    "2": 0.70,
    "3": 0.50,
}


@dataclass
class CalibrationRun:
    """Configuration and status for one calibration run."""

    run_id: UUID
    window_start: datetime
    window_end: datetime
    observation_extension_days: int = 90
    source_subset: list[str] | None = None  # None = all sources
    lens_subset: list[str] | None = None    # None = all lenses
    model_overrides: dict[str, str] = field(default_factory=dict)
    sandbox_prompt_template: str = "replay_sandbox_v1"
    status: CalibrationStatus = CalibrationStatus.CONFIGURED
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    notes: str = ""
    summary: dict[str, Any] | None = None

    @property
    def scoring_cutoff(self) -> datetime:
        """Signals extracted before this timestamp are eligible for scoring."""
        from datetime import timedelta
        return self.window_end


@dataclass
class SignalOutcomeRecord:
    """One resolved (or pending) signal outcome in a calibration run."""

    run_id: UUID
    signal_id: UUID
    source_id: str
    lens_id: str
    content_timestamp: datetime
    outcome: SignalOutcome = SignalOutcome.PENDING
    score: float | None = None
    resolved_at: datetime | None = None
    contributed_edge_id: UUID | None = None


@dataclass
class SourceScore:
    """Aggregated calibration score for one source."""

    source_id: str
    tier: str
    n_signals: int
    n_scored: int
    raw_score: float
    mean_score: float
    prior_weight: float
    tier_baseline: float
    proposed_weight: float
    outcome_breakdown: dict[str, int] = field(default_factory=dict)

    @property
    def weight_delta(self) -> float:
        return self.proposed_weight - self.prior_weight


@dataclass
class LensScore:
    """Aggregated calibration score for one lens."""

    lens_id: str
    n_signals: int
    n_scored: int
    raw_score: float
    mean_score: float
    outcome_breakdown: dict[str, int] = field(default_factory=dict)
    flagged: bool = False
    flag_reason: str = ""


@dataclass
class LeakageCheckResult:
    """Result of a leakage spot-check on a sample of extractions."""

    n_sampled: int
    n_suspicious: int
    suspicious_signal_ids: list[UUID] = field(default_factory=list)
    notes: str = ""

    @property
    def leakage_rate(self) -> float:
        if self.n_sampled == 0:
            return 0.0
        return self.n_suspicious / self.n_sampled


@dataclass
class CalibrationReport:
    """Full report produced at the end of a calibration run."""

    run_id: UUID
    window_start: datetime
    window_end: datetime
    generated_at: datetime

    n_signals_total: int
    n_signals_scored: int
    n_signals_pending: int

    source_scores: list[SourceScore] = field(default_factory=list)
    lens_scores: list[LensScore] = field(default_factory=list)
    leakage: LeakageCheckResult | None = None

    # Sources whose proposed weight deviates from prior by more than threshold
    flagged_sources: list[str] = field(default_factory=list)
    # Lenses with mean_score below the warning threshold
    flagged_lenses: list[str] = field(default_factory=list)

    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict (for storage in calibration_runs.summary)."""
        return {
            "run_id": str(self.run_id),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "generated_at": self.generated_at.isoformat(),
            "n_signals_total": self.n_signals_total,
            "n_signals_scored": self.n_signals_scored,
            "n_signals_pending": self.n_signals_pending,
            "leakage_rate": self.leakage.leakage_rate if self.leakage else None,
            "flagged_sources": self.flagged_sources,
            "flagged_lenses": self.flagged_lenses,
            "source_scores": [
                {
                    "source_id": s.source_id,
                    "tier": s.tier,
                    "n_signals": s.n_signals,
                    "mean_score": round(s.mean_score, 4),
                    "prior_weight": s.prior_weight,
                    "proposed_weight": round(s.proposed_weight, 4),
                    "weight_delta": round(s.weight_delta, 4),
                    "outcome_breakdown": s.outcome_breakdown,
                }
                for s in sorted(
                    self.source_scores, key=lambda x: x.mean_score, reverse=True
                )
            ],
            "lens_scores": [
                {
                    "lens_id": l.lens_id,
                    "n_signals": l.n_signals,
                    "mean_score": round(l.mean_score, 4),
                    "flagged": l.flagged,
                    "flag_reason": l.flag_reason,
                    "outcome_breakdown": l.outcome_breakdown,
                }
                for l in sorted(
                    self.lens_scores, key=lambda x: x.mean_score, reverse=True
                )
            ],
        }
