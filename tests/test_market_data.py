"""
Unit tests for the Yahoo Finance market-data client's deterministic logic.

The value of the market axis is that price moves become *deterministically*
computed signals (a %-change over a trailing window), not bare numbers handed
to an LLM. These tests pin that computation and the chart-response parsing —
no network required.
"""

from __future__ import annotations

import pytest

from augur.ingestion.api_clients.yahoo_finance import (
    _build_content,
    _extract_closes,
    compute_move,
)


# ── compute_move ──────────────────────────────────────────────────────────────


class TestComputeMove:
    def test_rise_over_window(self):
        closes = [100.0, 101, 102, 103, 104, 105]  # 5 trading days back = 100
        latest, prior, pct, used = compute_move(closes, window=5)
        assert latest == 105
        assert prior == 100.0
        assert pct == pytest.approx(5.0)
        assert used == 5

    def test_fall_over_window(self):
        closes = [200.0, 190, 180]
        latest, prior, pct, used = compute_move(closes, window=2)
        assert pct == pytest.approx(-10.0)
        assert used == 2

    def test_window_clamped_to_history(self):
        # Only 3 points but window 10 → clamp to the oldest available.
        closes = [50.0, 55, 60]
        latest, prior, pct, used = compute_move(closes, window=10)
        assert prior == 50.0
        assert used == 2
        assert pct == pytest.approx(20.0)

    def test_too_few_points_returns_none(self):
        assert compute_move([100.0], window=5) is None
        assert compute_move([], window=5) is None

    def test_zero_prior_returns_none(self):
        assert compute_move([0.0, 1.0, 2.0], window=2) is None

    def test_invalid_window_returns_none(self):
        assert compute_move([1.0, 2.0], window=0) is None


# ── _extract_closes ───────────────────────────────────────────────────────────


def _chart(closes, timestamps=None):
    return {
        "chart": {
            "result": [
                {
                    "timestamp": timestamps or [1, 2, 3],
                    "indicators": {"quote": [{"close": closes}]},
                }
            ]
        }
    }


class TestExtractCloses:
    def test_extracts_closes_and_last_timestamp(self):
        parsed = _extract_closes(_chart([10.0, 11.0, 12.0], [100, 200, 300]))
        assert parsed is not None
        closes, last_ts = parsed
        assert closes == [10.0, 11.0, 12.0]
        assert last_ts == 300

    def test_filters_null_closes(self):
        parsed = _extract_closes(_chart([10.0, None, 12.0]))
        closes, _ = parsed
        assert closes == [10.0, 12.0]

    def test_all_null_returns_none(self):
        assert _extract_closes(_chart([None, None])) is None

    def test_malformed_shape_returns_none(self):
        assert _extract_closes({}) is None
        assert _extract_closes({"chart": {"result": []}}) is None
        assert _extract_closes({"chart": {"result": [{"indicators": {}}]}}) is None


# ── _build_content ────────────────────────────────────────────────────────────


class TestBuildContent:
    def test_rise_statement(self):
        text = _build_content("Brent Crude", "BZ=F", 85.86, 82.40, 4.199, 5, "USD/bbl")
        assert "Brent Crude (BZ=F) rose +4.20%" in text
        assert "over the last 5 trading days" in text
        assert "USD/bbl" in text

    def test_fall_statement(self):
        text = _build_content("S&P 500", "^GSPC", 5000.0, 5250.0, -4.76, 5, "")
        assert "fell -4.76%" in text
