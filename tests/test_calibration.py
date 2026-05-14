"""
Tests for Phase 6: Calibration layer.

Covers:
  - CalibrationStatus / SignalOutcome enums and DEFAULT_OUTCOME_SCORES
  - TIER_BASELINES values
  - SourceScore.weight_delta
  - LeakageCheckResult.leakage_rate
  - CalibrationReport.to_dict()
  - build_sandbox_system_prompt() injection
  - _sandbox_lens() creates a new object without mutating original
  - _compute_new_weight() formula correctness
  - _is_suspicious() leakage heuristics
  - format_leakage_report() output shape
  - outcome_for_signal() logic via mocked DB connection
  - register_signals_for_run() with mocked pool
  - CalibrationOrchestrator.apply_weights() filtering and validation
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Models ─────────────────────────────────────────────────────────────────────


from augur.calibration.models import (
    DEFAULT_OUTCOME_SCORES,
    TIER_BASELINES,
    CalibrationReport,
    CalibrationRun,
    CalibrationStatus,
    LeakageCheckResult,
    LensScore,
    SignalOutcome,
    SourceScore,
)

_NOW = datetime(2023, 7, 1, 0, 0, 0, tzinfo=timezone.utc)
_START = datetime(2022, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
_END = datetime(2023, 6, 30, 0, 0, 0, tzinfo=timezone.utc)


class TestCalibrationStatus:
    def test_values(self):
        assert CalibrationStatus.CONFIGURED == "configured"
        assert CalibrationStatus.RUNNING == "running"
        assert CalibrationStatus.SCORING == "scoring"
        assert CalibrationStatus.COMPLETE == "complete"
        assert CalibrationStatus.FAILED == "failed"

    def test_is_str_enum(self):
        assert isinstance(CalibrationStatus.COMPLETE, str)


class TestSignalOutcome:
    def test_all_outcomes_present(self):
        outcomes = {o.value for o in SignalOutcome}
        assert "anchored_strengthened" in outcomes
        assert "anchored_persistent" in outcomes
        assert "anchored_weakened" in outcomes
        assert "anchored_deprecated" in outcomes
        assert "clustered_but_not_anchored" in outcomes
        assert "isolated_in_tier_a" in outcomes
        assert "extraction_rejected" in outcomes
        assert "pending" in outcomes

    def test_default_outcome_scores_cover_all_non_pending(self):
        for outcome in SignalOutcome:
            if outcome == SignalOutcome.PENDING:
                continue
            assert outcome in DEFAULT_OUTCOME_SCORES, f"{outcome} missing from DEFAULT_OUTCOME_SCORES"

    def test_score_signs(self):
        assert DEFAULT_OUTCOME_SCORES[SignalOutcome.ANCHORED_STRENGTHENED] > 0
        assert DEFAULT_OUTCOME_SCORES[SignalOutcome.ANCHORED_PERSISTENT] > 0
        assert DEFAULT_OUTCOME_SCORES[SignalOutcome.ANCHORED_WEAKENED] < 0
        assert DEFAULT_OUTCOME_SCORES[SignalOutcome.ANCHORED_DEPRECATED] < 0
        assert DEFAULT_OUTCOME_SCORES[SignalOutcome.CLUSTERED_BUT_NOT_ANCHORED] == 0.0
        assert DEFAULT_OUTCOME_SCORES[SignalOutcome.ISOLATED_IN_TIER_A] < 0
        assert DEFAULT_OUTCOME_SCORES[SignalOutcome.EXTRACTION_REJECTED] == 0.0

    def test_anchored_strengthened_is_highest(self):
        scored = {k: v for k, v in DEFAULT_OUTCOME_SCORES.items() if k != SignalOutcome.PENDING}
        assert DEFAULT_OUTCOME_SCORES[SignalOutcome.ANCHORED_STRENGTHENED] == max(scored.values())

    def test_anchored_deprecated_is_lowest(self):
        scored = {k: v for k, v in DEFAULT_OUTCOME_SCORES.items() if k != SignalOutcome.PENDING}
        assert DEFAULT_OUTCOME_SCORES[SignalOutcome.ANCHORED_DEPRECATED] == min(scored.values())


class TestTierBaselines:
    def test_structured_data_highest(self):
        assert TIER_BASELINES["structured_data"] >= max(
            v for k, v in TIER_BASELINES.items() if k != "structured_data"
        )

    def test_tier_3_lowest(self):
        assert TIER_BASELINES["3"] <= min(
            v for k, v in TIER_BASELINES.items() if k != "3"
        )

    def test_values_in_range(self):
        for k, v in TIER_BASELINES.items():
            assert 0.0 <= v <= 1.0, f"Tier baseline {k}={v} out of [0,1]"


class TestSourceScore:
    def _make(self, prior=0.5, proposed=0.65):
        return SourceScore(
            source_id="test_src",
            tier="2",
            n_signals=10,
            n_scored=8,
            raw_score=4.0,
            mean_score=0.5,
            prior_weight=prior,
            tier_baseline=0.70,
            proposed_weight=proposed,
        )

    def test_weight_delta_positive(self):
        s = self._make(prior=0.5, proposed=0.65)
        assert abs(s.weight_delta - 0.15) < 1e-9

    def test_weight_delta_negative(self):
        s = self._make(prior=0.7, proposed=0.55)
        assert abs(s.weight_delta - (-0.15)) < 1e-9

    def test_weight_delta_zero(self):
        s = self._make(prior=0.5, proposed=0.5)
        assert s.weight_delta == 0.0


class TestLeakageCheckResult:
    def test_leakage_rate_zero_sample(self):
        r = LeakageCheckResult(n_sampled=0, n_suspicious=0)
        assert r.leakage_rate == 0.0

    def test_leakage_rate_calculation(self):
        r = LeakageCheckResult(n_sampled=100, n_suspicious=5)
        assert abs(r.leakage_rate - 0.05) < 1e-9

    def test_leakage_rate_above_threshold(self):
        r = LeakageCheckResult(n_sampled=100, n_suspicious=10)
        assert r.leakage_rate > 0.05


class TestCalibrationReport:
    def _make_report(self):
        run_id = uuid.uuid4()
        s = SourceScore(
            source_id="src_a", tier="1",
            n_signals=50, n_scored=40,
            raw_score=30.0, mean_score=0.75,
            prior_weight=0.8, tier_baseline=0.80,
            proposed_weight=0.82,
            outcome_breakdown={"anchored_strengthened": 25},
        )
        l = LensScore(
            lens_id="financial",
            n_signals=20, n_scored=18,
            raw_score=10.0, mean_score=0.55,
            flagged=False,
        )
        leakage = LeakageCheckResult(n_sampled=50, n_suspicious=1)
        return CalibrationReport(
            run_id=run_id,
            window_start=_START,
            window_end=_END,
            generated_at=_NOW,
            n_signals_total=100,
            n_signals_scored=80,
            n_signals_pending=20,
            source_scores=[s],
            lens_scores=[l],
            leakage=leakage,
            flagged_sources=[],
            flagged_lenses=[],
        )

    def test_to_dict_keys(self):
        r = self._make_report()
        d = r.to_dict()
        assert "run_id" in d
        assert "source_scores" in d
        assert "lens_scores" in d
        assert "leakage_rate" in d
        assert "flagged_sources" in d
        assert "flagged_lenses" in d

    def test_to_dict_run_id_is_string(self):
        r = self._make_report()
        d = r.to_dict()
        assert isinstance(d["run_id"], str)

    def test_to_dict_leakage_rate(self):
        r = self._make_report()
        d = r.to_dict()
        assert abs(d["leakage_rate"] - 0.02) < 0.01

    def test_to_dict_source_score_fields(self):
        r = self._make_report()
        d = r.to_dict()
        ss = d["source_scores"][0]
        assert "source_id" in ss
        assert "proposed_weight" in ss
        assert "weight_delta" in ss
        assert "mean_score" in ss

    def test_to_dict_without_leakage(self):
        r = self._make_report()
        r.leakage = None
        d = r.to_dict()
        assert d["leakage_rate"] is None

    def test_to_dict_source_scores_sorted_by_mean_score_desc(self):
        run_id = uuid.uuid4()
        scores = [
            SourceScore("b", "2", 10, 8, 2.0, 0.25, 0.5, 0.7, 0.55),
            SourceScore("a", "1", 10, 8, 7.0, 0.875, 0.8, 0.8, 0.85),
        ]
        r = CalibrationReport(
            run_id=run_id, window_start=_START, window_end=_END,
            generated_at=_NOW, n_signals_total=20, n_signals_scored=16,
            n_signals_pending=4, source_scores=scores, lens_scores=[],
        )
        d = r.to_dict()
        mean_scores = [s["mean_score"] for s in d["source_scores"]]
        assert mean_scores == sorted(mean_scores, reverse=True)


# ── Replay / sandbox prompt ────────────────────────────────────────────────────


from augur.calibration.replay import (
    SANDBOX_PROMPT_TEMPLATE,
    _sandbox_lens,
    build_sandbox_system_prompt,
)


class TestBuildSandboxSystemPrompt:
    def test_prepends_date(self):
        replay_date = datetime(2022, 11, 15, tzinfo=timezone.utc)
        result = build_sandbox_system_prompt("base prompt text", replay_date)
        assert "2022-11-15" in result
        assert "base prompt text" in result

    def test_sandbox_before_base(self):
        replay_date = datetime(2022, 11, 15, tzinfo=timezone.utc)
        result = build_sandbox_system_prompt("BASE", replay_date)
        sandbox_pos = result.index("REPLAY MODE")
        base_pos = result.index("BASE")
        assert sandbox_pos < base_pos

    def test_replay_date_appears_multiple_times(self):
        replay_date = datetime(2023, 3, 1, tzinfo=timezone.utc)
        result = build_sandbox_system_prompt("anything", replay_date)
        assert result.count("2023-03-01") >= 2

    def test_separator_present(self):
        replay_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
        result = build_sandbox_system_prompt("text", replay_date)
        assert "---" in result


class TestSandboxLens:
    def _make_lens(self, prompt="original prompt"):
        import dataclasses

        from augur.extraction.lens import LensConfig, NodeType, EdgeType
        return LensConfig(
            lens_id="test_lens",
            lens_version="1",
            system_prompt=prompt,
            graph_scope_nodes=frozenset({NodeType.ENTITY}),
            graph_scope_edges=frozenset({EdgeType.CAUSES}),
            model_class="cheap",
            max_signals=5,
        )

    def test_sandbox_lens_prepends_instruction(self):
        lens = self._make_lens("original system prompt")
        replay_date = datetime(2022, 6, 1, tzinfo=timezone.utc)
        sandboxed = _sandbox_lens(lens, replay_date)
        assert "2022-06-01" in sandboxed.system_prompt
        assert "original system prompt" in sandboxed.system_prompt

    def test_sandbox_lens_does_not_mutate_original(self):
        lens = self._make_lens("original")
        replay_date = datetime(2022, 6, 1, tzinfo=timezone.utc)
        _sandbox_lens(lens, replay_date)
        assert lens.system_prompt == "original"

    def test_sandbox_lens_preserves_other_fields(self):
        lens = self._make_lens()
        replay_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
        sandboxed = _sandbox_lens(lens, replay_date)
        assert sandboxed.lens_id == lens.lens_id
        assert sandboxed.max_signals == lens.max_signals
        assert sandboxed.graph_scope_nodes == lens.graph_scope_nodes

    def test_sandbox_lens_returns_new_object(self):
        lens = self._make_lens()
        replay_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
        sandboxed = _sandbox_lens(lens, replay_date)
        assert sandboxed is not lens


# ── Scorer: _compute_new_weight ────────────────────────────────────────────────


from augur.calibration.scorer import _compute_new_weight


class TestComputeNewWeight:
    def test_formula_at_mean_zero(self):
        # mean_score=0 → scaled = 0.5/1.5 = 0.333
        # new = 0.5*0.7 + 0.3*0.5 + 0.2*0.333 = 0.35 + 0.15 + 0.0667 = 0.5667
        result = _compute_new_weight(mean_score=0.0, tier_baseline=0.7, prior_weight=0.5)
        assert abs(result - round(0.5 * 0.7 + 0.3 * 0.5 + 0.2 * (0.5 / 1.5), 4)) < 1e-4

    def test_perfect_score_increases_weight(self):
        # mean_score=1.0 → scaled = 1.5/1.5 = 1.0
        # new = 0.5*0.7 + 0.3*0.5 + 0.2*1.0 = 0.35 + 0.15 + 0.20 = 0.70
        result = _compute_new_weight(mean_score=1.0, tier_baseline=0.7, prior_weight=0.5)
        assert abs(result - 0.70) < 1e-4

    def test_terrible_score_decreases_weight(self):
        # mean_score=-0.5 → scaled = 0/1.5 = 0.0
        # new = 0.5*0.7 + 0.3*0.8 + 0.2*0.0 = 0.35 + 0.24 = 0.59
        result = _compute_new_weight(mean_score=-0.5, tier_baseline=0.7, prior_weight=0.8)
        assert abs(result - round(0.5 * 0.7 + 0.3 * 0.8, 4)) < 1e-4

    def test_result_clamped_to_zero_one(self):
        # Even extreme inputs shouldn't produce results outside [0, 1]
        r1 = _compute_new_weight(mean_score=10.0, tier_baseline=1.0, prior_weight=1.0)
        r2 = _compute_new_weight(mean_score=-10.0, tier_baseline=0.0, prior_weight=0.0)
        assert 0.0 <= r1 <= 1.0
        assert 0.0 <= r2 <= 1.0

    def test_result_is_rounded_to_4dp(self):
        result = _compute_new_weight(mean_score=0.333, tier_baseline=0.7, prior_weight=0.6)
        assert result == round(result, 4)

    def test_tier_baseline_dominates(self):
        # Coefficient 0.5 is highest — tier_baseline matters most
        high_baseline = _compute_new_weight(0.0, 0.9, 0.5)
        low_baseline = _compute_new_weight(0.0, 0.5, 0.9)
        assert high_baseline > low_baseline

    def test_scaled_score_clipped_at_zero(self):
        # mean_score below -0.5 should be treated same as -0.5
        r1 = _compute_new_weight(-0.5, 0.7, 0.5)
        r2 = _compute_new_weight(-2.0, 0.7, 0.5)
        assert r1 == r2


# ── Leakage heuristics ─────────────────────────────────────────────────────────


from augur.calibration.leakage import _is_suspicious, format_leakage_report


def _make_row(
    reasoning: str = "",
    claim_text: str = "",
    confidence_band: str = "weak",
    source_id: str = "src_a",
    access_method: str = "http",
) -> dict:
    return {
        "reasoning": reasoning,
        "claim_text": claim_text,
        "confidence_band": confidence_band,
        "source_id": source_id,
        "access_method": access_method,
    }


class TestIsSuspicious:
    WINDOW_END_YEAR = 2022

    def test_year_after_window_in_reasoning(self):
        row = _make_row(reasoning="This became clear in 2023.")
        assert _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_year_within_window_ok(self):
        # "Q3 2022" doesn't match suspicious temporal phrases and year <= window_end_year
        row = _make_row(reasoning="Supply disruption during Q3 2022 was notable.")
        assert not _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_year_after_window_in_claim(self):
        row = _make_row(claim_text="The merger closed in 2024.")
        assert _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_suspicious_phrase_has_since(self):
        row = _make_row(reasoning="The situation has since resolved.")
        assert _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_suspicious_phrase_would_later(self):
        row = _make_row(reasoning="They would later regret the decision.")
        assert _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_suspicious_phrase_later_revealed(self):
        row = _make_row(reasoning="It was later revealed that the data was wrong.")
        assert _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_suspicious_phrase_the_following_year(self):
        row = _make_row(reasoning="The following year saw record growth.")
        assert _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_hard_datum_rss_source(self):
        row = _make_row(
            confidence_band="hard_datum",
            source_id="bloomberg_rss",
        )
        assert _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_hard_datum_rss_access_method(self):
        row = _make_row(
            confidence_band="hard_datum",
            access_method="rss",
        )
        assert _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_hard_datum_non_rss_not_suspicious(self):
        row = _make_row(
            confidence_band="hard_datum",
            source_id="eia_api",
            access_method="api",
            reasoning="Oil production was 12.5 mbpd in Q1 2022.",
        )
        assert not _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_clean_row_not_suspicious(self):
        # No future years, no suspicious temporal phrases, not hard_datum+rss
        row = _make_row(
            reasoning="Supply constraints remain elevated.",
            claim_text="Production rose 3% Q3 2021.",
        )
        assert not _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)

    def test_none_values_handled(self):
        row = {"reasoning": None, "claim_text": None, "confidence_band": None,
               "source_id": None, "access_method": None}
        # Should not raise
        _is_suspicious(row, window_end_year=self.WINDOW_END_YEAR)


class TestFormatLeakageReport:
    def test_contains_key_fields(self):
        result = LeakageCheckResult(
            n_sampled=100, n_suspicious=3,
            suspicious_signal_ids=[],
            notes="Within threshold.",
        )
        text = format_leakage_report(result)
        assert "100" in text
        assert "3" in text
        assert "3.0%" in text

    def test_above_threshold_warning(self):
        result = LeakageCheckResult(n_sampled=100, n_suspicious=10)
        text = format_leakage_report(result)
        assert "ABOVE THRESHOLD" in text or "re-run" in text.lower()

    def test_within_threshold_ok(self):
        result = LeakageCheckResult(n_sampled=100, n_suspicious=2)
        text = format_leakage_report(result)
        assert "Within threshold" in text or "✓" in text

    def test_suspicious_ids_listed(self):
        ids = [uuid.uuid4() for _ in range(3)]
        result = LeakageCheckResult(n_sampled=50, n_suspicious=3, suspicious_signal_ids=ids)
        text = format_leakage_report(result)
        assert str(ids[0]) in text


# ── outcome_for_signal() logic ─────────────────────────────────────────────────


from augur.calibration.tracker import outcome_for_signal


def _make_conn(**kwargs) -> Any:
    """Create an async mock DB connection with configurable fetchrow/fetch results."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock()
    conn.fetch = AsyncMock()
    return conn


class TestOutcomeForSignal:
    @pytest.mark.asyncio
    async def test_anchored_deprecated(self):
        conn = _make_conn()
        signal_id = uuid.uuid4()
        edge_id = uuid.uuid4()
        conn.fetchrow.return_value = {
            "target_edge_id": edge_id,
            "deprecated": True,
            "current_weight_band": "disputed",
        }
        result = await outcome_for_signal(conn, signal_id=signal_id)
        assert result == SignalOutcome.ANCHORED_DEPRECATED

    @pytest.mark.asyncio
    async def test_anchored_strengthened(self):
        conn = _make_conn()
        signal_id = uuid.uuid4()
        edge_id = uuid.uuid4()
        conn.fetchrow.return_value = {
            "target_edge_id": edge_id,
            "deprecated": False,
            "current_weight_band": "strong",
        }
        conn.fetch.return_value = [
            {"change_type": "strengthened"},
            {"change_type": "initial"},
        ]
        result = await outcome_for_signal(conn, signal_id=signal_id)
        assert result == SignalOutcome.ANCHORED_STRENGTHENED

    @pytest.mark.asyncio
    async def test_anchored_weakened(self):
        conn = _make_conn()
        signal_id = uuid.uuid4()
        edge_id = uuid.uuid4()
        conn.fetchrow.return_value = {
            "target_edge_id": edge_id,
            "deprecated": False,
            "current_weight_band": "weak",
        }
        conn.fetch.return_value = [
            {"change_type": "weakened"},
        ]
        result = await outcome_for_signal(conn, signal_id=signal_id)
        assert result == SignalOutcome.ANCHORED_WEAKENED

    @pytest.mark.asyncio
    async def test_anchored_weakened_via_disconfirmation(self):
        conn = _make_conn()
        signal_id = uuid.uuid4()
        edge_id = uuid.uuid4()
        conn.fetchrow.return_value = {
            "target_edge_id": edge_id,
            "deprecated": False,
            "current_weight_band": "moderate",
        }
        conn.fetch.return_value = [
            {"change_type": "disconfirmation"},
        ]
        result = await outcome_for_signal(conn, signal_id=signal_id)
        assert result == SignalOutcome.ANCHORED_WEAKENED

    @pytest.mark.asyncio
    async def test_anchored_persistent(self):
        conn = _make_conn()
        signal_id = uuid.uuid4()
        edge_id = uuid.uuid4()
        conn.fetchrow.return_value = {
            "target_edge_id": edge_id,
            "deprecated": False,
            "current_weight_band": "moderate",
        }
        conn.fetch.return_value = [
            {"change_type": "initial"},
        ]
        result = await outcome_for_signal(conn, signal_id=signal_id)
        assert result == SignalOutcome.ANCHORED_PERSISTENT

    @pytest.mark.asyncio
    async def test_clustered_but_not_anchored(self):
        conn = _make_conn()
        signal_id = uuid.uuid4()
        # First fetchrow (graph_update_events) returns None
        # Second fetchrow (signals table) returns row with cluster_id set
        conn.fetchrow.side_effect = [
            None,  # not in graph_update_events
            {"cluster_id": uuid.uuid4()},  # signals table
        ]
        result = await outcome_for_signal(conn, signal_id=signal_id)
        assert result == SignalOutcome.CLUSTERED_BUT_NOT_ANCHORED

    @pytest.mark.asyncio
    async def test_isolated_in_tier_a(self):
        conn = _make_conn()
        signal_id = uuid.uuid4()
        conn.fetchrow.side_effect = [
            None,  # not in graph_update_events
            {"cluster_id": None},  # signals table, no cluster
        ]
        result = await outcome_for_signal(conn, signal_id=signal_id)
        assert result == SignalOutcome.ISOLATED_IN_TIER_A

    @pytest.mark.asyncio
    async def test_extraction_rejected(self):
        conn = _make_conn()
        signal_id = uuid.uuid4()
        conn.fetchrow.side_effect = [
            None,  # not in graph_update_events
            None,  # signal not found in signals table
        ]
        result = await outcome_for_signal(conn, signal_id=signal_id)
        assert result == SignalOutcome.EXTRACTION_REJECTED


# ── register_signals_for_run ────────────────────────────────────────────────────


from augur.calibration.tracker import register_signals_for_run


class TestRegisterSignalsForRun:
    @pytest.mark.asyncio
    async def test_empty_signals_returns_zero(self):
        pool = AsyncMock()
        result = await register_signals_for_run(pool, run_id=uuid.uuid4(), signals=[])
        assert result == 0

    @pytest.mark.asyncio
    async def test_registers_signals(self):
        run_id = uuid.uuid4()
        signals = [
            {
                "signal_id": uuid.uuid4(),
                "source_id": "src_a",
                "lens_id": "financial",
                "content_timestamp": _START,
            },
            {
                "signal_id": uuid.uuid4(),
                "source_id": "src_b",
                "lens_id": "geopolitical",
                "content_timestamp": _START,
            },
        ]
        conn = AsyncMock()
        conn.execute = AsyncMock(return_value=None)
        pool = AsyncMock()
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        result = await register_signals_for_run(pool, run_id=run_id, signals=signals)
        assert result == 2
        assert conn.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_exception_on_insert_is_skipped(self):
        run_id = uuid.uuid4()
        signals = [
            {
                "signal_id": uuid.uuid4(),
                "source_id": "src_a",
                "lens_id": "financial",
                "content_timestamp": _START,
            },
        ]
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=Exception("DB error"))
        pool = AsyncMock()
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        # Should not raise — failures are logged and skipped; failed inserts return 0
        result = await register_signals_for_run(pool, run_id=run_id, signals=signals)
        assert result == 0


# ── CalibrationOrchestrator.apply_weights ──────────────────────────────────────


from augur.calibration.orchestrator import CalibrationOrchestrator


def _make_run(status=CalibrationStatus.COMPLETE, summary=None):
    run_id = uuid.uuid4()
    default_summary = {
        "source_scores": [
            {"source_id": "src_a", "proposed_weight": 0.75},
            {"source_id": "src_b", "proposed_weight": 0.55},
        ]
    }
    return CalibrationRun(
        run_id=run_id,
        window_start=_START,
        window_end=_END,
        status=status,
        summary=summary if summary is not None else default_summary,
        created_at=_NOW,
    )


class TestApplyWeights:
    def _orchestrator(self):
        pool = AsyncMock()
        llm_client = AsyncMock()
        return CalibrationOrchestrator(pool, llm_client)

    @pytest.mark.asyncio
    async def test_raises_if_not_complete(self):
        orch = self._orchestrator()
        run = _make_run(status=CalibrationStatus.RUNNING)
        with pytest.raises(ValueError, match="not complete"):
            await orch.apply_weights(run)

    @pytest.mark.asyncio
    async def test_raises_if_no_summary(self):
        orch = self._orchestrator()
        run = _make_run(summary={})
        run.summary = None  # force None
        with pytest.raises(ValueError, match="no summary"):
            await orch.apply_weights(run)

    @pytest.mark.asyncio
    async def test_returns_all_weights_when_no_filter(self):
        orch = self._orchestrator()
        run = _make_run()
        result = await orch.apply_weights(run)
        assert result == {"src_a": 0.75, "src_b": 0.55}

    @pytest.mark.asyncio
    async def test_filters_to_requested_sources(self):
        orch = self._orchestrator()
        run = _make_run()
        result = await orch.apply_weights(run, source_ids=["src_a"])
        assert result == {"src_a": 0.75}
        assert "src_b" not in result

    @pytest.mark.asyncio
    async def test_returns_empty_when_filter_misses_all(self):
        orch = self._orchestrator()
        run = _make_run()
        result = await orch.apply_weights(run, source_ids=["nonexistent"])
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_source_scores(self):
        orch = self._orchestrator()
        run = _make_run(summary={"source_scores": []})
        result = await orch.apply_weights(run)
        assert result == {}


# ── CalibrationRun helpers ─────────────────────────────────────────────────────


class TestCalibrationRun:
    def test_scoring_cutoff_equals_window_end(self):
        run = CalibrationRun(
            run_id=uuid.uuid4(),
            window_start=_START,
            window_end=_END,
        )
        assert run.scoring_cutoff == _END

    def test_default_status_is_configured(self):
        run = CalibrationRun(
            run_id=uuid.uuid4(),
            window_start=_START,
            window_end=_END,
        )
        assert run.status == CalibrationStatus.CONFIGURED

    def test_default_observation_extension_days(self):
        run = CalibrationRun(
            run_id=uuid.uuid4(),
            window_start=_START,
            window_end=_END,
        )
        assert run.observation_extension_days == 90
