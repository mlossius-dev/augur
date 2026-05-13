"""
Unit tests for the extraction layer.

Tests:
  1. LensConfig: graph_scope enforcement
  2. Commodities lens: configuration sanity checks
  3. LensExecutor: parse valid LLM output, handle empty, handle malformed JSON
  4. LensExecutor: out-of-scope anchor filtering
  5. LensExecutor: max_signals cap
  6. TierAStore: deduplication (hash-based)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from augur.extraction.executor import (
    LensExecutor,
    _filter_anchors_to_scope,
    _parse_llm_output,
    _validate_signal,
)
from augur.extraction.lens import LensConfig
from augur.extraction.lenses.commodities import COMMODITIES_LENS
from augur.extraction.tier_a import TierAStore, _claim_hash
from augur.graph.schema import EdgeType, NodeType

_TS = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_PAYLOAD_ID = uuid.uuid4()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LensConfig sanity
# ═══════════════════════════════════════════════════════════════════════════════


class TestLensConfig:
    def test_commodities_lens_has_required_fields(self):
        lens = COMMODITIES_LENS
        assert lens.lens_id == "commodities"
        assert lens.lens_version == "1"
        assert len(lens.system_prompt) > 200
        assert len(lens.graph_scope_nodes) > 0
        assert len(lens.graph_scope_edges) > 0

    def test_commodities_lens_disallows_scenario(self):
        assert NodeType.SCENARIO not in COMMODITIES_LENS.graph_scope_nodes

    def test_commodities_lens_disallows_claim(self):
        assert NodeType.CLAIM not in COMMODITIES_LENS.graph_scope_nodes

    def test_commodities_lens_disallows_enables_edge(self):
        assert EdgeType.ENABLES not in COMMODITIES_LENS.graph_scope_edges

    def test_commodities_lens_allows_causes_edge(self):
        assert EdgeType.CAUSES in COMMODITIES_LENS.graph_scope_edges

    def test_max_signals_default(self):
        assert COMMODITIES_LENS.max_signals == 10


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LLM output parsing
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseLlmOutput:
    def test_parse_valid_json_array(self):
        content = json.dumps([{"claim_text": "Oil prices rose.", "confidence_band": "reported_claim"}])
        result = _parse_llm_output(content, lens_id="commodities")
        assert len(result) == 1
        assert result[0]["claim_text"] == "Oil prices rose."

    def test_parse_markdown_fenced(self):
        content = "```json\n" + json.dumps([{"claim_text": "Gas supply fell."}]) + "\n```"
        result = _parse_llm_output(content, lens_id="commodities")
        assert len(result) == 1

    def test_parse_empty_array(self):
        result = _parse_llm_output("[]", lens_id="commodities")
        assert result == []

    def test_parse_malformed_returns_empty(self):
        result = _parse_llm_output("This is not JSON at all.", lens_id="commodities")
        assert result == []

    def test_parse_extracts_embedded_array(self):
        content = "Some preamble text.\n[{\"claim_text\": \"X\"}]\nSome trailing text."
        result = _parse_llm_output(content, lens_id="commodities")
        assert len(result) == 1

    def test_parse_non_list_returns_empty(self):
        result = _parse_llm_output('{"claim_text": "X"}', lens_id="commodities")
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Signal validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateSignal:
    def test_valid_signal_passes(self):
        raw = {
            "claim_text": "WTI crude fell 3% on demand concerns.",
            "confidence_band": "reported_claim",
            "reasoning": "Reuters reported the price move.",
            "proposed_anchors": [],
        }
        result = _validate_signal(raw, lens=COMMODITIES_LENS)
        assert result is not None
        assert result["claim_text"] == "WTI crude fell 3% on demand concerns."
        assert result["confidence_band"] == "reported_claim"

    def test_missing_claim_text_returns_none(self):
        raw = {"confidence_band": "reported_claim"}
        assert _validate_signal(raw, lens=COMMODITIES_LENS) is None

    def test_empty_claim_text_returns_none(self):
        raw = {"claim_text": "   ", "confidence_band": "reported_claim"}
        assert _validate_signal(raw, lens=COMMODITIES_LENS) is None

    def test_invalid_confidence_band_falls_back(self):
        raw = {"claim_text": "Something happened.", "confidence_band": "made_up_band"}
        result = _validate_signal(raw, lens=COMMODITIES_LENS)
        assert result is not None
        assert result["confidence_band"] == "weak_inference"

    def test_too_long_claim_text_returns_none(self):
        raw = {"claim_text": "X" * 501, "confidence_band": "inference"}
        assert _validate_signal(raw, lens=COMMODITIES_LENS) is None

    def test_proposed_anchors_truncated_to_cap(self):
        anchors = [
            {"operation": "create_node", "node_type": "entity", "proposed_id": f"e{i}",
             "fields": {"name": f"Entity{i}", "entity_kind": "commodity"}, "reasoning": "test"}
            for i in range(15)
        ]
        raw = {
            "claim_text": "Multiple entities observed.",
            "confidence_band": "inference",
            "proposed_anchors": anchors,
        }
        result = _validate_signal(raw, lens=COMMODITIES_LENS)
        assert result is not None
        assert len(result["proposed_anchors"]) == 10  # MAX_ANCHORS_PER_SIGNAL


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Anchor scope filtering
# ═══════════════════════════════════════════════════════════════════════════════


class TestFilterAnchorsToScope:
    def test_allowed_node_type_passes(self):
        anchors = [
            {"operation": "create_node", "node_type": "entity",
             "proposed_id": "crude_oil", "fields": {"name": "Crude Oil", "entity_kind": "commodity"},
             "reasoning": "test"}
        ]
        result = _filter_anchors_to_scope(anchors, COMMODITIES_LENS)
        assert len(result) == 1

    def test_disallowed_node_type_filtered(self):
        anchors = [
            {"operation": "create_node", "node_type": "scenario",
             "proposed_id": "scen1", "fields": {"name": "Scenario A"}, "reasoning": "test"}
        ]
        result = _filter_anchors_to_scope(anchors, COMMODITIES_LENS)
        assert result == []

    def test_allowed_edge_type_passes(self):
        anchors = [
            {"operation": "create_edge", "edge_type": "causes",
             "source_node_id": "a", "target_node_id": "b",
             "proposed_weight_band": "moderate", "reasoning": "test",
             "falsification_criteria": "FC."}
        ]
        result = _filter_anchors_to_scope(anchors, COMMODITIES_LENS)
        assert len(result) == 1

    def test_disallowed_edge_type_filtered(self):
        anchors = [
            {"operation": "create_edge", "edge_type": "contradicts",
             "source_node_id": "a", "target_node_id": "b",
             "proposed_weight_band": "weak", "reasoning": "test",
             "falsification_criteria": "FC."}
        ]
        result = _filter_anchors_to_scope(anchors, COMMODITIES_LENS)
        assert result == []

    def test_update_operations_pass_through(self):
        anchors = [
            {"operation": "update_node", "target_node_id": "some-uuid",
             "field_updates": {"description": "updated"}, "reasoning": "test"},
            {"operation": "update_edge_weight", "target_edge_id": "edge-uuid",
             "new_weight_band": "strong", "direction": "strengthen", "reasoning": "test"},
        ]
        result = _filter_anchors_to_scope(anchors, COMMODITIES_LENS)
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 5. LensExecutor — mock LLM
# ═══════════════════════════════════════════════════════════════════════════════


_GOOD_LLM_RESPONSE = json.dumps([
    {
        "claim_text": "WTI crude fell 3% on increased OPEC output.",
        "confidence_band": "reported_claim",
        "reasoning": "Reuters reported price move linked to OPEC decision.",
        "proposed_anchors": [
            {
                "operation": "create_node",
                "node_type": "entity",
                "proposed_id": "crude_oil",
                "fields": {"name": "Crude Oil", "entity_kind": "commodity"},
                "reasoning": "Primary commodity mentioned.",
            }
        ],
    }
])


class TestLensExecutor:
    def _make_executor(self, llm_response_content: str = _GOOD_LLM_RESPONSE) -> LensExecutor:
        llm_response = MagicMock()
        llm_response.content = llm_response_content
        llm_response.model = "test-model"
        llm_response.prompt_tokens = 100
        llm_response.completion_tokens = 50

        llm_client = AsyncMock()
        llm_client.complete = AsyncMock(return_value=llm_response)
        return LensExecutor(llm_client)

    @pytest.mark.asyncio
    async def test_extract_returns_signals(self):
        executor = self._make_executor()
        signals = await executor.extract(
            payload_id=_PAYLOAD_ID,
            content="WTI crude oil fell 3% on Thursday.",
            content_timestamp=_TS,
            source_id="reuters_rss",
            lens=COMMODITIES_LENS,
        )
        assert len(signals) == 1
        assert signals[0]["claim_text"] == "WTI crude fell 3% on increased OPEC output."
        assert signals[0]["lens_id"] == "commodities"
        assert signals[0]["payload_id"] == _PAYLOAD_ID

    @pytest.mark.asyncio
    async def test_extract_returns_empty_for_empty_response(self):
        executor = self._make_executor("[]")
        signals = await executor.extract(
            payload_id=_PAYLOAD_ID,
            content="Unrelated content about sports.",
            content_timestamp=_TS,
            source_id="reuters_rss",
            lens=COMMODITIES_LENS,
        )
        assert signals == []

    @pytest.mark.asyncio
    async def test_extract_returns_empty_on_llm_failure(self):
        llm_client = AsyncMock()
        llm_client.complete = AsyncMock(side_effect=Exception("LLM unavailable"))
        executor = LensExecutor(llm_client)

        signals = await executor.extract(
            payload_id=_PAYLOAD_ID,
            content="Some content.",
            content_timestamp=_TS,
            source_id="reuters_rss",
            lens=COMMODITIES_LENS,
        )
        assert signals == []

    @pytest.mark.asyncio
    async def test_extract_enforces_max_signals_cap(self):
        # 15 signals in response, lens allows 10
        many_signals = json.dumps([
            {
                "claim_text": f"Claim {i}: commodity signal.",
                "confidence_band": "inference",
                "reasoning": "test",
                "proposed_anchors": [],
            }
            for i in range(15)
        ])
        executor = self._make_executor(many_signals)
        signals = await executor.extract(
            payload_id=_PAYLOAD_ID,
            content="Very long article about commodities.",
            content_timestamp=_TS,
            source_id="reuters_rss",
            lens=COMMODITIES_LENS,
        )
        assert len(signals) <= COMMODITIES_LENS.max_signals

    @pytest.mark.asyncio
    async def test_extract_all_lenses_merges_results(self):
        executor = self._make_executor(_GOOD_LLM_RESPONSE)
        signals = await executor.extract_all_lenses(
            payload_id=_PAYLOAD_ID,
            content="Oil prices moved.",
            content_timestamp=_TS,
            source_id="reuters_rss",
            lenses=[COMMODITIES_LENS],
        )
        assert len(signals) == 1

    @pytest.mark.asyncio
    async def test_required_signal_fields_present(self):
        executor = self._make_executor(_GOOD_LLM_RESPONSE)
        signals = await executor.extract(
            payload_id=_PAYLOAD_ID,
            content="Oil moved.",
            content_timestamp=_TS,
            source_id="reuters_rss",
            lens=COMMODITIES_LENS,
        )
        required = {"signal_id", "payload_id", "lens_id", "lens_version",
                    "claim_text", "confidence_band", "proposed_anchors",
                    "content_timestamp", "extracted_at"}
        assert required.issubset(set(signals[0].keys()))


# ═══════════════════════════════════════════════════════════════════════════════
# 6. TierAStore — deduplication
# ═══════════════════════════════════════════════════════════════════════════════


class TestTierADeduplication:
    @pytest.mark.asyncio
    async def test_deduplicate_removes_exact_duplicates(self):
        pool = MagicMock()  # not used in dedup test
        store = TierAStore(pool)

        sig_a = {
            "signal_id": uuid.uuid4(),
            "claim_text": "WTI crude fell 3%.",
            "payload_id": _PAYLOAD_ID,
        }
        sig_b = {
            "signal_id": uuid.uuid4(),
            "claim_text": "WTI crude fell 3%.",  # same text
            "payload_id": _PAYLOAD_ID,
        }
        sig_c = {
            "signal_id": uuid.uuid4(),
            "claim_text": "Wheat prices rose 2%.",  # different
            "payload_id": _PAYLOAD_ID,
        }

        result = await store.deduplicate_batch([sig_a, sig_b, sig_c])
        assert len(result) == 2
        claims = [r["claim_text"] for r in result]
        assert "WTI crude fell 3%." in claims
        assert "Wheat prices rose 2%." in claims

    @pytest.mark.asyncio
    async def test_deduplicate_case_insensitive(self):
        pool = MagicMock()
        store = TierAStore(pool)
        sig_a = {"signal_id": uuid.uuid4(), "claim_text": "WTI CRUDE FELL.", "payload_id": _PAYLOAD_ID}
        sig_b = {"signal_id": uuid.uuid4(), "claim_text": "wti crude fell.", "payload_id": _PAYLOAD_ID}
        result = await store.deduplicate_batch([sig_a, sig_b])
        assert len(result) == 1

    def test_claim_hash_consistent(self):
        h1 = _claim_hash("WTI crude fell 3%.")
        h2 = _claim_hash("WTI crude fell 3%.")
        assert h1 == h2

    def test_claim_hash_case_insensitive(self):
        assert _claim_hash("WTI CRUDE") == _claim_hash("wti crude")
