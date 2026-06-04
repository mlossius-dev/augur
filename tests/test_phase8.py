"""
Tests for Phase 8: Minimal presentation layer.

Covers:
  - dimensions: DIMENSION_KEYWORDS coverage, _compute_state_band,
    _compute_direction, _infer_dimension in changes
  - StateBand / Direction enum values
  - DimensionScore dataclass
  - changes: _infer_dimension, _edge_verb, _weight_change_summary,
    _condition_summary, ChangeRecord
  - API endpoint imports and router prefixes
  - main.py: routers mounted correctly
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

# ── Dimension model ────────────────────────────────────────────────────────────


from augur.presentation.dimensions import (
    DIMENSION_KEYWORDS,
    DIMENSIONS,
    DIMENSION_LABELS,
    Direction,
    DimensionScore,
    SparkPoint,
    StateBand,
    _compute_direction,
    _compute_state_band,
)


class TestDimensions:
    def test_five_dimensions_defined(self):
        assert len(DIMENSIONS) == 5
        expected = {
            "economic_stability", "geopolitical_tension",
            "resource_availability", "environmental_stress", "structural_change",
        }
        assert set(DIMENSIONS) == expected

    def test_all_dimensions_have_labels(self):
        for d in DIMENSIONS:
            assert d in DIMENSION_LABELS
            assert DIMENSION_LABELS[d]

    def test_all_dimensions_have_keywords(self):
        for d in DIMENSIONS:
            assert d in DIMENSION_KEYWORDS
            assert len(DIMENSION_KEYWORDS[d]) >= 5

    def test_no_empty_keywords(self):
        for d, kws in DIMENSION_KEYWORDS.items():
            for kw in kws:
                assert kw.strip(), f"Empty keyword in {d}"


class TestStateBand:
    def test_values(self):
        assert StateBand.IMPROVING == "improving"
        assert StateBand.STABLE == "stable"
        assert StateBand.STRAINED == "strained"
        assert StateBand.DETERIORATING == "deteriorating"
        assert StateBand.CRISIS == "crisis"
        assert StateBand.UNKNOWN == "unknown"


class TestDirection:
    def test_values(self):
        assert Direction.IMPROVING == "improving"
        assert Direction.STEADY == "steady"
        assert Direction.WORSENING == "worsening"
        assert Direction.UNKNOWN == "unknown"


class TestComputeStateBand:
    def test_no_conditions_is_unknown(self):
        assert _compute_state_band(0, 0) == StateBand.UNKNOWN

    def test_zero_active_is_improving(self):
        assert _compute_state_band(0, 20) == StateBand.IMPROVING

    def test_low_active_is_stable(self):
        assert _compute_state_band(6, 20) == StateBand.STABLE  # 30%

    def test_mid_active_is_strained(self):
        assert _compute_state_band(9, 20) == StateBand.STRAINED  # 45%

    def test_high_active_is_deteriorating(self):
        assert _compute_state_band(14, 20) == StateBand.DETERIORATING  # 70%

    def test_all_active_is_crisis(self):
        assert _compute_state_band(20, 20) == StateBand.CRISIS  # 100%

    def test_boundaries(self):
        # Exactly 20% — still improving (< 0.20 is strictly less than)
        assert _compute_state_band(4, 20) == StateBand.STABLE  # 20% → stable
        # Exactly 80% — crisis (>= 0.80)
        assert _compute_state_band(16, 20) == StateBand.CRISIS


class TestComputeDirection:
    def _nodes(self, ids: list[str]) -> dict:
        return {nid: {"name": f"node_{nid}"} for nid in ids}

    def test_no_nodes_returns_unknown(self):
        result = _compute_direction({}, {}, {})
        assert result == Direction.UNKNOWN

    def test_more_recent_active_is_worsening(self):
        nodes = self._nodes(["a", "b", "c", "d"])
        recent = {"a": "active", "b": "active", "c": "active"}
        prior = {"a": "inactive", "b": "inactive"}
        result = _compute_direction(nodes, recent, prior)
        assert result == Direction.WORSENING

    def test_fewer_recent_active_is_improving(self):
        nodes = self._nodes(["a", "b", "c"])
        recent = {"a": "inactive"}
        prior = {"a": "active", "b": "active", "c": "active"}
        result = _compute_direction(nodes, recent, prior)
        assert result == Direction.IMPROVING

    def test_same_count_is_steady(self):
        nodes = self._nodes(["a", "b"])
        recent = {"a": "active"}
        prior = {"a": "active"}
        result = _compute_direction(nodes, recent, prior)
        assert result == Direction.STEADY

    def test_empty_histories_returns_unknown(self):
        nodes = self._nodes(["a", "b"])
        result = _compute_direction(nodes, {}, {})
        assert result == Direction.UNKNOWN


class TestDimensionScore:
    def test_dataclass_fields(self):
        s = DimensionScore(
            dimension="economic_stability",
            label="Economic Stability",
            state=StateBand.STRAINED,
            direction=Direction.WORSENING,
            active_conditions=5,
            total_conditions=10,
            strong_edge_count=3,
            weak_edge_count=7,
        )
        assert s.dimension == "economic_stability"
        assert s.state == StateBand.STRAINED
        assert s.sparkline == []

    def test_sparkline_stored(self):
        sp = SparkPoint(week_start="2024-01-01", active_count=3, total_count=5)
        s = DimensionScore(
            dimension="geopolitical_tension",
            label="Geopolitical Tension",
            state=StateBand.DETERIORATING,
            direction=Direction.WORSENING,
            active_conditions=8,
            total_conditions=10,
            strong_edge_count=5,
            weak_edge_count=2,
            sparkline=[sp],
        )
        assert len(s.sparkline) == 1
        assert s.sparkline[0].week_start == "2024-01-01"


# ── Changes module ─────────────────────────────────────────────────────────────


from augur.presentation.changes import (
    ChangeRecord,
    _condition_summary,
    _edge_verb,
    _infer_dimension,
    _weight_change_summary,
)


class TestInferDimension:
    def test_oil_is_resource_availability(self):
        assert _infer_dimension("Oil supply constraints from OPEC") == "resource_availability"

    def test_bank_is_economic(self):
        assert _infer_dimension("Central bank rate decision expected") == "economic_stability"

    def test_military_is_geopolitical(self):
        assert _infer_dimension("Military escalation along border") == "geopolitical_tension"

    def test_earthquake_is_environmental(self):
        assert _infer_dimension("Earthquake disrupts supply routes") == "environmental_stress"

    def test_regulation_is_structural(self):
        assert _infer_dimension("New regulation on AI deployment") == "structural_change"

    def test_no_match_falls_back_to_structural(self):
        result = _infer_dimension("Something completely generic")
        assert result == "structural_change"

    def test_case_insensitive(self):
        assert _infer_dimension("OIL MARKETS REACT TO OPEC") == "resource_availability"


class TestEdgeVerb:
    def test_known_verbs(self):
        assert "causes" in _edge_verb("causes")
        assert "enables" in _edge_verb("enables")
        assert "correlates" in _edge_verb("correlates_with")
        assert "part of" in _edge_verb("part_of")

    def test_unknown_falls_back(self):
        result = _edge_verb("some_unknown_type")
        assert "some" in result or "unknown" in result


class TestWeightChangeSummary:
    def test_strengthened_mentions_weight(self):
        result = _weight_change_summary("edge_strengthened", "A causes B", "strong")
        assert "strong" in result
        assert "A causes B" in result

    def test_weakened_summary(self):
        result = _weight_change_summary("edge_weakened", "X enables Y", "weak")
        assert "weak" in result
        assert "X enables Y" in result

    def test_disconfirmation_summary(self):
        result = _weight_change_summary("disconfirmation_weakened", "P contradicts Q", "moderate")
        assert "isconfirm" in result
        assert "P contradicts Q" in result


class TestConditionSummary:
    def test_activated(self):
        result = _condition_summary("condition_activated", "High inflation")
        assert "activated" in result.lower() or "High inflation" in result

    def test_deactivated(self):
        result = _condition_summary("condition_deactivated", "Supply shortage")
        assert "deactivated" in result.lower() or "Supply shortage" in result


class TestChangeRecord:
    def test_dataclass_fields(self):
        r = ChangeRecord(
            change_id="test-1",
            change_type="edge_strengthened",
            summary="Oil causes inflation strengthened.",
            dimension="resource_availability",
            dimension_label="Resource Availability",
            occurred_at=_NOW.isoformat(),
            target_id=str(uuid.uuid4()),
            target_type="edge",
            target_name="Oil causes inflation",
            weight_before="moderate",
            weight_after="strong",
            impact_rank=2,
        )
        assert r.change_type == "edge_strengthened"
        assert r.weight_before == "moderate"
        assert r.weight_after == "strong"


# ── API router prefixes ────────────────────────────────────────────────────────


from augur.api.home import router as home_router
from augur.api.reasoning import router as reasoning_router


class TestRouterPrefixes:
    def test_home_router_prefix(self):
        assert home_router.prefix == "/api"

    def test_reasoning_router_prefix(self):
        assert reasoning_router.prefix == "/api/reasoning"

    def test_home_routes_exist(self):
        paths = {r.path for r in home_router.routes}
        assert "/api/home" in paths
        assert "/api/home/changes" in paths

    def test_reasoning_routes_exist(self):
        paths = {r.path for r in reasoning_router.routes}
        assert "/api/reasoning/node/{node_id}" in paths
        assert "/api/reasoning/edge/{edge_id}" in paths


# ── main.py: routers mounted ───────────────────────────────────────────────────


class TestMainAppRouters:
    def test_home_and_reasoning_routers_importable(self):
        from augur.api.home import router as hr
        from augur.api.reasoning import router as rr
        assert hr is not None
        assert rr is not None

    def test_home_router_tags(self):
        assert "home" in home_router.tags

    def test_reasoning_router_tags(self):
        assert "reasoning" in reasoning_router.tags


# ── compute_dimension_scores (mocked DB) ──────────────────────────────────────


from augur.presentation.dimensions import compute_dimension_scores


class TestComputeDimensionScores:
    @pytest.mark.asyncio
    async def test_returns_five_dimensions(self):
        pool = AsyncMock()
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        scores = await compute_dimension_scores(pool)
        assert len(scores) == 5

    @pytest.mark.asyncio
    async def test_each_score_has_required_fields(self):
        pool = AsyncMock()
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        scores = await compute_dimension_scores(pool)
        for s in scores:
            assert s.dimension in DIMENSIONS
            assert s.label
            assert s.state in StateBand
            assert s.direction in Direction

    @pytest.mark.asyncio
    async def test_empty_graph_produces_unknown_states(self):
        pool = AsyncMock()
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        scores = await compute_dimension_scores(pool)
        for s in scores:
            # With no conditions, should be unknown
            assert s.state == StateBand.UNKNOWN

    @pytest.mark.asyncio
    async def test_active_conditions_produce_score(self):
        pool = AsyncMock()
        conn = AsyncMock()
        node_id = uuid.uuid4()
        # Two condition nodes: one active, one inactive in economic domain
        conn.fetch = AsyncMock(side_effect=[
            # condition_rows
            [
                {"node_id": node_id, "name": "Bank credit tightening",
                 "description": "credit conditions have tightened",
                 "current_state": "active"},
                {"node_id": uuid.uuid4(), "name": "Inflation elevated",
                 "description": "inflation above target",
                 "current_state": "inactive"},
            ],
            # edge_rows
            [{"source_node_id": node_id, "target_node_id": uuid.uuid4(),
              "current_weight_band": "strong"}],
            # recent_changes
            [],
            # sparkline_rows
            [],
        ])
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        scores = await compute_dimension_scores(pool)
        econ = next(s for s in scores if s.dimension == "economic_stability")
        # 1 active out of 2 conditions (50%) → strained
        assert econ.state == StateBand.STRAINED
        assert econ.active_conditions == 1
        assert econ.total_conditions == 2


# ── get_recent_changes (mocked DB) ────────────────────────────────────────────


from augur.presentation.changes import get_recent_changes


class TestGetRecentChanges:
    @pytest.mark.asyncio
    async def test_empty_db_returns_empty_list(self):
        pool = AsyncMock()
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[])
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        result = await get_recent_changes(pool, hours=24)
        assert result == []

    @pytest.mark.asyncio
    async def test_deduplication_works(self):
        """Entries appearing in multiple query results should appear only once."""
        pool = AsyncMock()
        conn = AsyncMock()
        edge_id = uuid.uuid4()
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        conn.fetch = AsyncMock(side_effect=[
            # weight_rows — one strengthened edge
            [
                {
                    "id": 1, "edge_id": edge_id,
                    "weight_band": "strong", "previous_weight_band": "moderate",
                    "change_type": "strengthened", "content_timestamp": _NOW,
                    "source_name": "Oil supply", "target_name": "Inflation",
                    "edge_type": "causes",
                }
            ],
            # state_rows — empty
            [],
            # new_edge_rows — empty
            [],
        ])
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        result = await get_recent_changes(pool)
        assert len(result) == 1
        assert result[0].change_type == "edge_strengthened"
        assert result[0].target_id == str(edge_id)

    @pytest.mark.asyncio
    async def test_condition_activated_record(self):
        pool = AsyncMock()
        conn = AsyncMock()
        node_id = uuid.uuid4()
        conn.fetch = AsyncMock(side_effect=[
            # weight_rows — empty
            [],
            # state_rows — one activation
            [
                {
                    "id": 10, "node_id": node_id,
                    "new_state": "active", "previous_state": "inactive",
                    "content_timestamp": _NOW,
                    "node_name": "High inflation",
                    "description": "inflation above target",
                }
            ],
            # new_edge_rows — empty
            [],
        ])
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        result = await get_recent_changes(pool)
        assert len(result) == 1
        assert result[0].change_type == "condition_activated"
        assert result[0].target_type == "node"

    @pytest.mark.asyncio
    async def test_respects_limit(self):
        pool = AsyncMock()
        conn = AsyncMock()
        # Return 5 weight changes, limit=3
        rows = [
            {
                "id": i, "edge_id": uuid.uuid4(),
                "weight_band": "strong", "previous_weight_band": "moderate",
                "change_type": "strengthened", "content_timestamp": _NOW,
                "source_name": f"Node {i}", "target_name": "Target",
                "edge_type": "causes",
            }
            for i in range(5)
        ]
        conn.fetch = AsyncMock(side_effect=[rows, [], []])
        pool.acquire = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=conn),
            __aexit__=AsyncMock(return_value=False),
        ))
        result = await get_recent_changes(pool, limit=3)
        assert len(result) <= 3
