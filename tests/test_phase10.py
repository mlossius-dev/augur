"""
Phase 10 tests — scenario projection layer.

Covers:
  - projection/models.py      — dataclasses and enums
  - projection/evidence.py    — gather_evidence DB queries
  - projection/parser.py      — parse_scenarios from LLM output
  - projection/store.py       — save_scenarios / get_scenarios
  - projection/prompts.py     — build_user_message
  - projection/orchestrator.py — ProjectionOrchestrator (LLM mocked)
  - api/scenarios.py          — list and detail endpoints
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_pool(*fetch_results, fetchrow_result=None, execute_result="UPDATE 0"):
    pool = MagicMock()
    conn = AsyncMock()

    call_count = {"n": 0}

    async def fetch_side_effect(query, *args):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(fetch_results):
            return fetch_results[idx]
        return []

    conn.fetch.side_effect = fetch_side_effect
    conn.fetchrow.return_value = fetchrow_result
    conn.execute.return_value = execute_result
    conn.executemany.return_value = execute_result

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool, conn


def _make_scenarios_client(mock_pool=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import augur.api.scenarios as sc_mod
    from augur.api.scenarios import router

    app = FastAPI()
    app.include_router(router)
    pool = mock_pool or MagicMock()
    app.dependency_overrides[sc_mod._pool] = lambda: pool
    return TestClient(app), pool


# ── Models ────────────────────────────────────────────────────────────────────


class TestProbabilityBand:
    def test_all_values_defined(self):
        from augur.projection.models import ProbabilityBand
        assert set(ProbabilityBand) == {"high", "moderate", "low", "negligible"}

    def test_strEnum_values(self):
        from augur.projection.models import ProbabilityBand
        assert ProbabilityBand.HIGH == "high"
        assert ProbabilityBand.NEGLIGIBLE == "negligible"


class TestScenarioDataclass:
    def test_scenario_instantiation(self):
        from augur.projection.models import ProbabilityBand, Scenario

        now = datetime.now(timezone.utc).isoformat()
        s = Scenario(
            scenario_id=str(uuid.uuid4()),
            dimension="economic_stability",
            title="Test scenario",
            summary="A test summary.",
            probability_band=ProbabilityBand.MODERATE,
            time_horizon="3–6 months",
            key_condition_ids=[],
            supporting_edge_ids=[],
            contradicting_edge_ids=[],
            generated_at=now,
            as_of=now,
        )
        assert s.title == "Test scenario"
        assert s.probability_band == ProbabilityBand.MODERATE
        assert not s.deprecated


# ── Parser ────────────────────────────────────────────────────────────────────


class TestParseScenarios:
    def _valid_raw(self, n=2):
        items = [
            {
                "title": f"Scenario {i}",
                "summary": "Something happens because of X.",
                "probability_band": ["high", "moderate", "low"][i % 3],
                "time_horizon": "3–6 months",
                "supporting_evidence": ["active condition A"],
                "contradicting_evidence": [],
            }
            for i in range(n)
        ]
        return json.dumps(items)

    def test_parses_valid_json(self):
        from augur.projection.parser import parse_scenarios

        raw = self._valid_raw(3)
        scenarios, err = parse_scenarios(raw, dimension="economic_stability", model_used="test-model")
        assert err is None
        assert len(scenarios) == 3

    def test_assigns_correct_fields(self):
        from augur.projection.parser import parse_scenarios

        raw = json.dumps([{
            "title": "Rate shock cascade",
            "summary": "Central banks overshoot.",
            "probability_band": "high",
            "time_horizon": "1-3 months",
            "supporting_evidence": [],
            "contradicting_evidence": [],
        }])
        scenarios, err = parse_scenarios(raw, dimension="economic_stability", model_used="m")
        assert err is None
        assert scenarios[0].title == "Rate shock cascade"
        assert scenarios[0].probability_band == "high"
        assert scenarios[0].time_horizon == "1-3 months"
        assert scenarios[0].dimension == "economic_stability"

    def test_strips_markdown_fences(self):
        from augur.projection.parser import parse_scenarios

        raw = "```json\n" + self._valid_raw(1) + "\n```"
        scenarios, err = parse_scenarios(raw, dimension=None, model_used="m")
        assert err is None
        assert len(scenarios) == 1

    def test_invalid_json_returns_error(self):
        from augur.projection.parser import parse_scenarios

        scenarios, err = parse_scenarios("not json", dimension=None, model_used="m")
        assert err is not None
        assert scenarios == []

    def test_non_array_returns_error(self):
        from augur.projection.parser import parse_scenarios

        scenarios, err = parse_scenarios('{"key": "val"}', dimension=None, model_used="m")
        assert err is not None

    def test_unknown_probability_band_defaults_to_moderate(self):
        from augur.projection.parser import parse_scenarios

        raw = json.dumps([{
            "title": "T", "summary": "S.",
            "probability_band": "extreme",
            "time_horizon": "6-12 months",
            "supporting_evidence": [], "contradicting_evidence": [],
        }])
        scenarios, err = parse_scenarios(raw, dimension=None, model_used="m")
        assert err is None
        assert scenarios[0].probability_band == "moderate"

    def test_skips_items_missing_title_or_summary(self):
        from augur.projection.parser import parse_scenarios

        raw = json.dumps([
            {"title": "", "summary": "S.", "probability_band": "low",
             "time_horizon": "6m", "supporting_evidence": [], "contradicting_evidence": []},
            {"title": "T", "summary": "S.", "probability_band": "low",
             "time_horizon": "6m", "supporting_evidence": [], "contradicting_evidence": []},
        ])
        scenarios, err = parse_scenarios(raw, dimension=None, model_used="m")
        assert len(scenarios) == 1

    def test_empty_array_returns_error(self):
        from augur.projection.parser import parse_scenarios

        scenarios, err = parse_scenarios("[]", dimension=None, model_used="m")
        assert err is not None

    def test_each_scenario_gets_unique_id(self):
        from augur.projection.parser import parse_scenarios

        raw = self._valid_raw(3)
        scenarios, _ = parse_scenarios(raw, dimension=None, model_used="m")
        ids = [s.scenario_id for s in scenarios]
        assert len(set(ids)) == 3


# ── Store ─────────────────────────────────────────────────────────────────────


class TestSaveScenarios:
    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_list(self):
        from augur.projection.store import save_scenarios

        pool, _ = _make_pool()
        result = await save_scenarios(pool, [])
        assert result == 0

    @pytest.mark.asyncio
    async def test_calls_executemany(self):
        from augur.projection.models import ProbabilityBand, Scenario
        from augur.projection.store import save_scenarios

        now = datetime.now(timezone.utc).isoformat()
        s = Scenario(
            scenario_id=str(uuid.uuid4()), dimension="economic_stability",
            title="T", summary="S.", probability_band=ProbabilityBand.LOW,
            time_horizon="3m", key_condition_ids=[], supporting_edge_ids=[],
            contradicting_edge_ids=[], generated_at=now, as_of=now,
        )
        pool, conn = _make_pool()
        result = await save_scenarios(pool, [s], deprecate_previous=False)
        assert result == 1
        assert conn.executemany.called

    @pytest.mark.asyncio
    async def test_deprecates_previous_when_requested(self):
        from augur.projection.models import ProbabilityBand, Scenario
        from augur.projection.store import save_scenarios

        now = datetime.now(timezone.utc).isoformat()
        s = Scenario(
            scenario_id=str(uuid.uuid4()), dimension="economic_stability",
            title="T", summary="S.", probability_band=ProbabilityBand.LOW,
            time_horizon="3m", key_condition_ids=[], supporting_edge_ids=[],
            contradicting_edge_ids=[], generated_at=now, as_of=now,
        )
        pool, conn = _make_pool()
        await save_scenarios(pool, [s], deprecate_previous=True, dimension="economic_stability")
        assert conn.execute.called  # UPDATE deprecated = TRUE


class TestGetScenarios:
    @pytest.mark.asyncio
    async def test_returns_empty_list(self):
        from augur.projection.store import get_scenarios

        pool, _ = _make_pool([])
        result = await get_scenarios(pool)
        assert result == []

    @pytest.mark.asyncio
    async def test_maps_row_to_scenario(self):
        from augur.projection.store import get_scenarios

        sid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        row = {
            "scenario_id": sid,
            "dimension": "economic_stability",
            "title": "Rate shock",
            "summary": "Central banks raise rates.",
            "probability_band": "high",
            "time_horizon": "3–6 months",
            "key_condition_ids": [],
            "supporting_edge_ids": [],
            "contradicting_edge_ids": [],
            "generated_at": now,
            "as_of": now,
            "model_used": "test",
            "deprecated": False,
        }
        pool, _ = _make_pool([row])
        result = await get_scenarios(pool)
        assert len(result) == 1
        assert result[0].title == "Rate shock"
        assert result[0].probability_band == "high"

    @pytest.mark.asyncio
    async def test_dimension_filter_included_in_query(self):
        from augur.projection.store import get_scenarios

        pool, conn = _make_pool([])
        await get_scenarios(pool, dimension="geopolitical_tension")
        call_args = conn.fetch.call_args
        assert "geopolitical_tension" in str(call_args)


# ── Evidence ──────────────────────────────────────────────────────────────────


class TestGatherEvidence:
    @pytest.mark.asyncio
    async def test_returns_evidence_with_empty_graph(self):
        from augur.projection.evidence import gather_evidence

        pool, _ = _make_pool([], [], [])
        evidence = await gather_evidence(pool, dimension="economic_stability")
        assert evidence.dimension == "economic_stability"
        assert evidence.active_conditions == []
        assert evidence.strong_edges == []
        assert evidence.recent_changes == []

    @pytest.mark.asyncio
    async def test_filters_conditions_by_dimension_keywords(self):
        from augur.projection.evidence import gather_evidence

        now = datetime.now(timezone.utc)
        conditions = [
            {"node_id": uuid.uuid4(), "name": "Bank credit tightening",
             "description": "Banks restricting lending", "current_state": "active"},
            {"node_id": uuid.uuid4(), "name": "Military escalation in region X",
             "description": "Conflict intensifying", "current_state": "active"},
        ]
        pool, _ = _make_pool(conditions, [], [])
        evidence = await gather_evidence(pool, dimension="economic_stability")
        names = [c["name"] for c in evidence.active_conditions]
        assert "Bank credit tightening" in names
        # "Military escalation" should be excluded from economic_stability
        assert "Military escalation in region X" not in names

    @pytest.mark.asyncio
    async def test_no_dimension_filter_returns_all(self):
        from augur.projection.evidence import gather_evidence

        conditions = [
            {"node_id": uuid.uuid4(), "name": "Bank credit tightening",
             "description": "", "current_state": "active"},
            {"node_id": uuid.uuid4(), "name": "Military escalation",
             "description": "", "current_state": "active"},
        ]
        pool, _ = _make_pool(conditions, [], [])
        evidence = await gather_evidence(pool, dimension=None)
        assert len(evidence.active_conditions) == 2

    @pytest.mark.asyncio
    async def test_caps_conditions_at_max(self):
        from augur.projection.evidence import gather_evidence, _MAX_CONDITIONS

        conditions = [
            {"node_id": uuid.uuid4(), "name": f"Bank rate item {i}",
             "description": "bank lending", "current_state": "active"}
            for i in range(_MAX_CONDITIONS + 10)
        ]
        pool, _ = _make_pool(conditions, [], [])
        evidence = await gather_evidence(pool, dimension="economic_stability")
        assert len(evidence.active_conditions) <= _MAX_CONDITIONS


# ── Prompts ───────────────────────────────────────────────────────────────────


class TestBuildUserMessage:
    def _evidence(self):
        from augur.projection.models import GraphEvidence

        return GraphEvidence(
            dimension="economic_stability",
            active_conditions=[
                {"node_id": "abc12345", "name": "Bank runs", "description": "Liquidity crisis"},
            ],
            strong_edges=[
                {"edge_id": "def67890", "source_name": "Bank runs",
                 "target_name": "Credit freeze", "edge_type": "causes", "weight_band": "strong"},
            ],
            recent_changes=[
                {"summary": "Bank runs causes Credit freeze — strengthened",
                 "change_type": "strengthened", "occurred_at": "2024-01-15T00:00:00Z"},
            ],
        )

    def test_includes_dimension_label(self):
        from augur.projection.prompts import build_user_message

        msg = build_user_message(self._evidence(), dimension_label="Economic Stability")
        assert "Economic Stability" in msg

    def test_includes_active_condition_names(self):
        from augur.projection.prompts import build_user_message

        msg = build_user_message(self._evidence(), dimension_label=None)
        assert "Bank runs" in msg

    def test_includes_edge_description(self):
        from augur.projection.prompts import build_user_message

        msg = build_user_message(self._evidence(), dimension_label=None)
        assert "Credit freeze" in msg
        assert "causes" in msg

    def test_handles_empty_evidence(self):
        from augur.projection.models import GraphEvidence
        from augur.projection.prompts import build_user_message

        empty = GraphEvidence(dimension=None, active_conditions=[], strong_edges=[], recent_changes=[])
        msg = build_user_message(empty, dimension_label=None)
        assert "(none)" in msg

    def test_cross_cutting_label(self):
        from augur.projection.models import GraphEvidence
        from augur.projection.prompts import build_user_message

        evidence = GraphEvidence(dimension=None, active_conditions=[], strong_edges=[], recent_changes=[])
        msg = build_user_message(evidence, dimension_label=None)
        assert "Cross-cutting" in msg or "global" in msg.lower()


# ── Orchestrator ──────────────────────────────────────────────────────────────


class TestProjectionOrchestrator:
    def _make_orch(self, llm_response_content):
        from augur.projection.orchestrator import ProjectionOrchestrator

        pool, conn = _make_pool([], [], [])  # empty graph

        llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = llm_response_content
        mock_response.model = "test-model"
        llm.complete = AsyncMock(return_value=mock_response)

        orch = ProjectionOrchestrator(pool, llm)
        return orch, pool

    @pytest.mark.asyncio
    async def test_run_projection_calls_llm(self):
        import json as _json
        raw = _json.dumps([{
            "title": "T1", "summary": "S1.",
            "probability_band": "moderate", "time_horizon": "3m",
            "supporting_evidence": [], "contradicting_evidence": [],
        }])
        orch, _ = self._make_orch(raw)
        result = await orch.run_projection(dimension="economic_stability", save=False)
        assert result.dimension == "economic_stability"
        assert len(result.scenarios) == 1
        assert result.model_used == "test-model"

    @pytest.mark.asyncio
    async def test_invalid_dimension_raises(self):
        orch, _ = self._make_orch("[]")
        with pytest.raises(ValueError, match="Unknown dimension"):
            await orch.run_projection(dimension="made_up", save=False)

    @pytest.mark.asyncio
    async def test_run_all_dimensions_calls_each(self):
        import json as _json
        raw = _json.dumps([{
            "title": "T", "summary": "S.",
            "probability_band": "low", "time_horizon": "6m",
            "supporting_evidence": [], "contradicting_evidence": [],
        }])
        orch, pool = self._make_orch(raw)
        # Re-create pool so fetch always returns []
        pool2, conn2 = _make_pool(*[[] for _ in range(30)])
        mock_response = MagicMock()
        mock_response.content = raw
        mock_response.model = "test"
        orch._pool = pool2
        orch._llm.complete = AsyncMock(return_value=mock_response)

        results = await orch.run_all_dimensions()
        assert len(results) == 5  # one per dimension

    @pytest.mark.asyncio
    async def test_parse_error_returns_empty_scenarios(self):
        orch, _ = self._make_orch("not json at all")
        result = await orch.run_projection(dimension=None, save=False)
        assert result.scenarios == []


# ── API ───────────────────────────────────────────────────────────────────────


class TestScenariosApiList:
    def test_returns_200_with_empty_list(self):
        client, _ = _make_scenarios_client()
        with patch("augur.api.scenarios.get_scenarios", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = []
            resp = client.get("/api/scenarios")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scenarios"] == []
        assert data["count"] == 0

    def test_serialises_scenario_fields(self):
        from augur.projection.models import ProbabilityBand, Scenario

        now = datetime.now(timezone.utc).isoformat()
        s = Scenario(
            scenario_id=str(uuid.uuid4()), dimension="economic_stability",
            title="Rate shock", summary="Rates rise sharply.",
            probability_band=ProbabilityBand.HIGH, time_horizon="3–6 months",
            key_condition_ids=[], supporting_edge_ids=[], contradicting_edge_ids=[],
            generated_at=now, as_of=now, model_used="test",
        )
        client, _ = _make_scenarios_client()
        with patch("augur.api.scenarios.get_scenarios", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = [s]
            resp = client.get("/api/scenarios")
        assert resp.status_code == 200
        data = resp.json()["scenarios"]
        assert len(data) == 1
        assert data[0]["title"] == "Rate shock"
        assert data[0]["probability_band"] == "high"

    def test_dimension_filter_passed_through(self):
        client, _ = _make_scenarios_client()
        with patch("augur.api.scenarios.get_scenarios", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = []
            resp = client.get("/api/scenarios?dimension=economic_stability")
        assert resp.status_code == 200
        call_kwargs = mock_fn.call_args[1]
        assert call_kwargs.get("dimension") == "economic_stability"

    def test_rejects_bad_as_of(self):
        client, _ = _make_scenarios_client()
        with patch("augur.api.scenarios.get_scenarios", new_callable=AsyncMock):
            resp = client.get("/api/scenarios?as_of=bad-date")
        assert resp.status_code == 422


class TestScenariosApiDetail:
    def test_returns_404_for_unknown_id(self):
        client, _ = _make_scenarios_client()
        with patch("augur.api.scenarios.get_scenarios", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = []
            resp = client.get(f"/api/scenarios/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_returns_scenario_by_id(self):
        from augur.projection.models import ProbabilityBand, Scenario

        now = datetime.now(timezone.utc).isoformat()
        sid = str(uuid.uuid4())
        s = Scenario(
            scenario_id=sid, dimension="geopolitical_tension",
            title="Alliance fracture", summary="Key alliance weakens.",
            probability_band=ProbabilityBand.MODERATE, time_horizon="6–12 months",
            key_condition_ids=[], supporting_edge_ids=[], contradicting_edge_ids=[],
            generated_at=now, as_of=now, model_used="test",
        )
        client, _ = _make_scenarios_client()
        with patch("augur.api.scenarios.get_scenarios", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = [s]
            resp = client.get(f"/api/scenarios/{sid}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Alliance fracture"


# ── Router registration ───────────────────────────────────────────────────────


class TestRouterRegistration:
    def test_scenarios_router_importable(self):
        from augur.api.scenarios import router
        assert router is not None

    def test_scenarios_router_has_expected_routes(self):
        from augur.api.scenarios import router

        paths = {r.path for r in router.routes}
        assert "/api/scenarios" in paths
        assert "/api/scenarios/{scenario_id}" in paths
