"""
Unit tests for the Phase 4 lens catalog.

Tests:
  1. Lens catalog: all six new lenses plus commodities export from ACTIVE_LENSES
  2. Lens configuration sanity: graph_scope_nodes/edges, model_class, max_signals
  3. Disconfirmation lens: system prompt builder, scope enforcement
  4. LensExecutor: extract_disconfirmation with mock LLM
  5. detect_cross_lens_convergence: same hash → converges, different hash → no convergence
  6. EMSC client: feature-to-result conversion
  7. Ingestion pipeline: http method dispatches to EmscClient for emsc_earthquakes
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from augur.extraction.executor import LensExecutor, detect_cross_lens_convergence
from augur.extraction.lenses import (
    ACTIVE_LENSES,
    COMMODITIES_LENS,
    DISCONFIRMATION_LENS,
    FINANCIAL_LENS,
    GEOPOLITICAL_LENS,
    NARRATIVE_DIVERGENCE_LENS,
    PHYSICAL_WORLD_LENS,
    REGULATORY_LENS,
    build_disconfirmation_system_prompt,
)
from augur.graph.schema import EdgeType, NodeType

_TS = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_PAYLOAD_ID = uuid.uuid4()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. ACTIVE_LENSES catalog
# ═══════════════════════════════════════════════════════════════════════════════


class TestActiveLensesCatalog:
    def test_all_six_standard_lenses_present(self):
        lens_ids = {l.lens_id for l in ACTIVE_LENSES}
        expected = {
            "commodities", "financial", "geopolitical",
            "physical_world", "regulatory", "narrative_divergence",
        }
        assert expected == lens_ids

    def test_disconfirmation_not_in_active_lenses(self):
        assert not any(l.lens_id == "disconfirmation" for l in ACTIVE_LENSES)

    def test_all_lenses_have_required_fields(self):
        for lens in ACTIVE_LENSES:
            assert lens.lens_id
            assert lens.lens_version
            assert len(lens.system_prompt) > 100, f"{lens.lens_id} system_prompt too short"
            assert lens.max_signals > 0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Individual lens configuration sanity
# ═══════════════════════════════════════════════════════════════════════════════


class TestFinancialLens:
    def test_allows_claim_node(self):
        assert NodeType.CLAIM in FINANCIAL_LENS.graph_scope_nodes

    def test_allows_all_edge_types(self):
        # Financial lens can use all nine edge types
        all_edges = set(EdgeType)
        assert all_edges == FINANCIAL_LENS.graph_scope_edges

    def test_disallows_scenario(self):
        assert NodeType.SCENARIO not in FINANCIAL_LENS.graph_scope_nodes

    def test_max_signals(self):
        assert FINANCIAL_LENS.max_signals == 10


class TestGeopoliticalLens:
    def test_allows_claim_node(self):
        assert NodeType.CLAIM in GEOPOLITICAL_LENS.graph_scope_nodes

    def test_disallows_quantity_node(self):
        assert NodeType.QUANTITY not in GEOPOLITICAL_LENS.graph_scope_nodes

    def test_disallows_produces_edge(self):
        assert EdgeType.PRODUCES not in GEOPOLITICAL_LENS.graph_scope_edges

    def test_allows_causes_edge(self):
        assert EdgeType.CAUSES in GEOPOLITICAL_LENS.graph_scope_edges


class TestPhysicalWorldLens:
    def test_only_event_and_quantity_nodes(self):
        assert PHYSICAL_WORLD_LENS.graph_scope_nodes == frozenset(
            {NodeType.EVENT, NodeType.QUANTITY}
        )

    def test_only_causes_and_correlates_edges(self):
        assert PHYSICAL_WORLD_LENS.graph_scope_edges == frozenset(
            {EdgeType.CAUSES, EdgeType.CORRELATES_WITH}
        )

    def test_max_signals(self):
        assert PHYSICAL_WORLD_LENS.max_signals == 8


class TestRegulatoryLens:
    def test_disallows_scenario_and_claim_nodes(self):
        assert NodeType.SCENARIO not in REGULATORY_LENS.graph_scope_nodes
        assert NodeType.CLAIM not in REGULATORY_LENS.graph_scope_nodes

    def test_allows_entity_condition_event_nodes(self):
        assert NodeType.ENTITY in REGULATORY_LENS.graph_scope_nodes
        assert NodeType.CONDITION in REGULATORY_LENS.graph_scope_nodes
        assert NodeType.EVENT in REGULATORY_LENS.graph_scope_nodes

    def test_disallows_contradicts_edge(self):
        assert EdgeType.CONTRADICTS not in REGULATORY_LENS.graph_scope_edges

    def test_allows_constrains_edge(self):
        assert EdgeType.CONSTRAINS in REGULATORY_LENS.graph_scope_edges


class TestNarrativeDivergenceLens:
    def test_only_claim_node(self):
        assert NARRATIVE_DIVERGENCE_LENS.graph_scope_nodes == frozenset({NodeType.CLAIM})

    def test_only_contradicts_edge(self):
        assert NARRATIVE_DIVERGENCE_LENS.graph_scope_edges == frozenset({EdgeType.CONTRADICTS})

    def test_max_signals(self):
        assert NARRATIVE_DIVERGENCE_LENS.max_signals == 6


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Disconfirmation lens
# ═══════════════════════════════════════════════════════════════════════════════


class TestDisconfirmationLens:
    def test_empty_graph_scope_nodes(self):
        assert DISCONFIRMATION_LENS.graph_scope_nodes == frozenset()

    def test_empty_graph_scope_edges(self):
        assert DISCONFIRMATION_LENS.graph_scope_edges == frozenset()

    def test_max_signals(self):
        assert DISCONFIRMATION_LENS.max_signals == 5

    def test_build_system_prompt_includes_edge_context(self):
        edge_context = "1. Edge `abc-123`: **Oil** --causes--> **Inflation** [strong]\n   *Falsification*: If CPI falls."
        prompt = build_disconfirmation_system_prompt(edge_context)
        assert "abc-123" in prompt
        assert "Falsification" in prompt
        assert "disconfirmation" in prompt.lower()

    def test_build_system_prompt_includes_output_constraints(self):
        prompt = build_disconfirmation_system_prompt("No edges.")
        assert "add_disconfirming_signal" in prompt
        assert "update_edge_weight" in prompt
        assert "CANNOT create new nodes" in prompt

    def test_filter_anchors_blocks_create_node(self):
        """Disconfirmation lens has empty scope so create_node is filtered."""
        from augur.extraction.executor import _filter_anchors_to_scope

        anchors = [
            {"operation": "create_node", "node_type": "entity",
             "proposed_id": "x", "fields": {"name": "X"}, "reasoning": "test"},
        ]
        result = _filter_anchors_to_scope(anchors, DISCONFIRMATION_LENS)
        assert result == []

    def test_filter_anchors_allows_add_disconfirming(self):
        """add_disconfirming_signal passes through scope filter unconditionally."""
        from augur.extraction.executor import _filter_anchors_to_scope

        anchors = [
            {"operation": "add_disconfirming_signal",
             "target_edge_id": str(uuid.uuid4()),
             "signal_id": str(uuid.uuid4())},
        ]
        result = _filter_anchors_to_scope(anchors, DISCONFIRMATION_LENS)
        assert len(result) == 1

    def test_filter_anchors_allows_update_edge_weight(self):
        from augur.extraction.executor import _filter_anchors_to_scope

        anchors = [
            {"operation": "update_edge_weight",
             "target_edge_id": str(uuid.uuid4()),
             "new_weight_band": "weak",
             "direction": "weaken",
             "reasoning": "Evidence against."},
        ]
        result = _filter_anchors_to_scope(anchors, DISCONFIRMATION_LENS)
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 4. LensExecutor.extract_disconfirmation
# ═══════════════════════════════════════════════════════════════════════════════


def _make_llm_mock(content: str = "[]"):
    response = MagicMock()
    response.content = content
    response.model = "test"
    response.prompt_tokens = 100
    response.completion_tokens = 20
    response.langfuse_trace_id = "trace-x"

    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=response)
    return llm


_DISCONF_RESPONSE = json.dumps([{
    "claim_text": "US CPI fell despite high oil prices, contradicting the causes edge.",
    "confidence_band": "reported_claim",
    "reasoning": "BLS data shows CPI -0.2% month-over-month.",
    "proposed_anchors": [
        {
            "operation": "add_disconfirming_signal",
            "target_edge_id": "edge-uuid-001",
            "signal_id": "",
        }
    ],
}])


class TestExtractDisconfirmation:
    @pytest.mark.asyncio
    async def test_no_edges_returns_empty(self):
        executor = LensExecutor(_make_llm_mock())
        result = await executor.extract_disconfirmation(
            payload_id=_PAYLOAD_ID,
            content="Some content.",
            content_timestamp=_TS,
            source_id="test_source",
            edge_context_rows=[],
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_disconfirmation_signals(self):
        executor = LensExecutor(_make_llm_mock(_DISCONF_RESPONSE))
        edge_rows = [{
            "edge_id": "edge-uuid-001",
            "source_name": "Oil",
            "target_name": "Inflation",
            "edge_type": "causes",
            "weight_band": "strong",
            "falsification_criteria": "If CPI falls while oil prices are high.",
        }]
        result = await executor.extract_disconfirmation(
            payload_id=_PAYLOAD_ID,
            content="BLS data shows CPI -0.2%.",
            content_timestamp=_TS,
            source_id="bls",
            edge_context_rows=edge_rows,
        )
        assert len(result) == 1
        assert result[0]["lens_id"] == "disconfirmation"
        assert "CPI" in result[0]["claim_text"]

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self):
        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=Exception("timeout"))
        executor = LensExecutor(llm)
        result = await executor.extract_disconfirmation(
            payload_id=_PAYLOAD_ID,
            content="Some content.",
            content_timestamp=_TS,
            source_id="test",
            edge_context_rows=[{"edge_id": "e1", "source_name": "A",
                                 "target_name": "B", "edge_type": "causes",
                                 "weight_band": "strong", "falsification_criteria": "FC"}],
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_create_node_anchors_filtered_out(self):
        # Even if LLM tries to create a node, it gets filtered by scope
        bad_response = json.dumps([{
            "claim_text": "Contradicting claim.",
            "confidence_band": "inference",
            "reasoning": "Test.",
            "proposed_anchors": [
                {"operation": "create_node", "node_type": "entity",
                 "proposed_id": "x", "fields": {"name": "X"}, "reasoning": "bad"},
                {"operation": "add_disconfirming_signal",
                 "target_edge_id": "e1", "signal_id": ""},
            ],
        }])
        executor = LensExecutor(_make_llm_mock(bad_response))
        result = await executor.extract_disconfirmation(
            payload_id=_PAYLOAD_ID,
            content="Content.",
            content_timestamp=_TS,
            source_id="test",
            edge_context_rows=[{"edge_id": "e1", "source_name": "A",
                                 "target_name": "B", "edge_type": "causes",
                                 "weight_band": "strong", "falsification_criteria": "FC"}],
        )
        assert len(result) == 1
        anchors = result[0]["proposed_anchors"]
        # create_node should be gone, add_disconfirming_signal should survive
        assert all(a["operation"] != "create_node" for a in anchors)
        assert any(a["operation"] == "add_disconfirming_signal" for a in anchors)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. detect_cross_lens_convergence
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectCrossLensConvergence:
    def _make_signal(self, claim: str, lens_id: str) -> dict:
        return {
            "signal_id": uuid.uuid4(),
            "lens_id": lens_id,
            "claim_text": claim,
        }

    def test_same_claim_different_lenses_converges(self):
        signals = [
            self._make_signal("Oil prices rose 5%.", "commodities"),
            self._make_signal("Oil prices rose 5%.", "financial"),
        ]
        result = detect_cross_lens_convergence(signals)
        assert len(result) == 1

    def test_different_claims_no_convergence(self):
        signals = [
            self._make_signal("Oil prices rose.", "commodities"),
            self._make_signal("Wheat prices fell.", "commodities"),
        ]
        result = detect_cross_lens_convergence(signals)
        assert result == {}

    def test_same_claim_same_lens_no_convergence(self):
        # Two signals from the SAME lens with identical text → not cross-lens
        signals = [
            self._make_signal("Oil prices rose.", "commodities"),
            self._make_signal("Oil prices rose.", "commodities"),
        ]
        result = detect_cross_lens_convergence(signals)
        assert result == {}

    def test_case_insensitive_matching(self):
        signals = [
            self._make_signal("OIL PRICES ROSE 5%.", "commodities"),
            self._make_signal("oil prices rose 5%.", "financial"),
        ]
        result = detect_cross_lens_convergence(signals)
        assert len(result) == 1

    def test_three_lens_convergence(self):
        claim = "US sanctions imposed on Iran oil exports."
        signals = [
            self._make_signal(claim, "commodities"),
            self._make_signal(claim, "geopolitical"),
            self._make_signal(claim, "regulatory"),
        ]
        result = detect_cross_lens_convergence(signals)
        assert len(result) == 1
        group = list(result.values())[0]
        assert len(group) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 6. EMSC client
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmscClient:
    def _make_source(self) -> Any:
        from augur.ingestion.models import SourceConfig
        return SourceConfig(
            source_id="emsc_earthquakes",
            canonical_name="EMSC",
            url_base="https://www.seismicportal.eu",
            tier="structured_data",
            perspective="us_eu",
            languages=["en"],
            access_method="http",
            access_config={
                "url": "https://www.seismicportal.eu/fdsnws/event/1/query?format=json&limit=50&minmag=4.5"
            },
            update_cadence="hourly",
            domains=["physical_world"],
            starting_source_weight=0.95,
        )

    @pytest.mark.asyncio
    async def test_fetch_returns_one_result_per_event(self):
        from augur.ingestion.api_clients.emsc import EmscClient

        mock_response = {
            "features": [
                {
                    "id": "20240601_0000001",
                    "properties": {
                        "unid": "20240601_0000001",
                        "mag": 6.2,
                        "flynn_region": "TURKEY",
                        "depth": 10.0,
                        "time": "2024-06-01T08:00:00Z",
                    },
                    "geometry": {"type": "Point", "coordinates": [36.5, 37.2, -10.0]},
                },
                {
                    "id": "20240601_0000002",
                    "properties": {
                        "unid": "20240601_0000002",
                        "mag": 5.1,
                        "flynn_region": "AEGEAN SEA",
                        "depth": 5.0,
                        "time": "2024-06-01T09:00:00Z",
                    },
                    "geometry": {"type": "Point", "coordinates": [26.1, 39.5, -5.0]},
                },
            ]
        }

        client = EmscClient()
        source = self._make_source()

        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(return_value=mock_resp))
            )
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            results = await client.fetch_source(source)

        assert len(results) == 2
        assert results[0].source_id == "emsc_earthquakes"
        assert "6.2" in results[0].raw_content or "M6.2" in results[0].raw_content
        assert "TURKEY" in results[0].raw_content

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        from augur.ingestion.api_clients.emsc import EmscClient

        client = EmscClient()
        source = self._make_source()

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_httpx.return_value.__aenter__ = AsyncMock(
                return_value=MagicMock(get=AsyncMock(side_effect=Exception("connection refused")))
            )
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)
            results = await client.fetch_source(source)

        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# 7. extract_all_lenses runs all active lenses
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractAllLenses:
    @pytest.mark.asyncio
    async def test_runs_all_active_lenses(self):
        """extract_all_lenses should call the LLM once per lens."""
        response = MagicMock()
        response.content = "[]"
        response.model = "test"
        response.prompt_tokens = 10
        response.completion_tokens = 5
        response.langfuse_trace_id = "t"

        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=response)

        executor = LensExecutor(llm)
        signals = await executor.extract_all_lenses(
            payload_id=_PAYLOAD_ID,
            content="Oil prices rose after OPEC decision.",
            content_timestamp=_TS,
            source_id="reuters_rss",
            lenses=ACTIVE_LENSES,
        )
        # All lenses returned empty — just verify the call count
        assert llm.complete.call_count == len(ACTIVE_LENSES)
        assert signals == []
