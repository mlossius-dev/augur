"""
Unit tests for the anchoring layer.

Tests:
  1. batch_former: form_batches — topical grouping, size cap, min-size filter
  2. batch_former: AnchorBatch — content_timestamp, signal_ids helpers
  3. orchestrator: _parse_anchor_operations — valid JSON, markdown fence, empty,
     malformed, partial-valid, discriminated union dispatch
  4. orchestrator: _extract_json_array — various input shapes
  5. orchestrator: _render_subgraph_context — smoke test
  6. prompt: build_system_prompt, build_user_message — content checks
  7. AnchoringOrchestrator: run_batch — mock LLM + Applier integration
  8. AnchoringOrchestrator: run_cycle — no signals path
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from augur.anchoring.batch_former import (
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
    AnchorBatch,
    _extract_entity_names,
    form_batches,
)
from augur.anchoring.orchestrator import (
    AnchoringOrchestrator,
    _extract_all_entity_names,
    _extract_json_array,
    _parse_anchor_operations,
    _render_subgraph_context,
)
from augur.anchoring.prompt import build_system_prompt, build_user_message

_TS = datetime(2024, 3, 1, 12, 0, 0, tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _make_signal(
    claim: str = "WTI crude fell 3%.",
    lens_id: str = "commodities",
    entity_names: list[str] | None = None,
    content_timestamp: datetime = _TS,
) -> dict[str, Any]:
    anchors = []
    for name in (entity_names or []):
        anchors.append({
            "operation": "create_node",
            "node_type": "entity",
            "proposed_id": name.lower().replace(" ", "_"),
            "fields": {"name": name, "entity_kind": "commodity"},
            "reasoning": "test",
        })
    return {
        "signal_id": uuid.uuid4(),
        "lens_id": lens_id,
        "lens_version": "1",
        "claim_text": claim,
        "confidence_band": "reported_claim",
        "content_timestamp": content_timestamp,
        "extracted_at": datetime.now(timezone.utc),
        "proposed_anchors": anchors,
        "anchored": False,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. form_batches — topical grouping
# ═══════════════════════════════════════════════════════════════════════════════


class TestFormBatches:
    def test_empty_input_returns_empty(self):
        assert form_batches([]) == []

    def test_signals_with_shared_entity_grouped(self):
        s1 = _make_signal("Crude up.", entity_names=["Crude Oil"])
        s2 = _make_signal("Crude supply cut.", entity_names=["Crude Oil", "OPEC"])
        s3 = _make_signal("Wheat rose.", entity_names=["Wheat"])

        batches = form_batches([s1, s2, s3], force=True)
        # s1 and s2 share "crude oil"; s3 is separate
        assert len(batches) == 2
        group_sizes = sorted(len(b.signals) for b in batches)
        assert group_sizes == [1, 2]

    def test_signals_with_no_shared_entity_separate(self):
        s1 = _make_signal("Oil.", entity_names=["Crude Oil"])
        s2 = _make_signal("Wheat.", entity_names=["Wheat"])
        s3 = _make_signal("Gold.", entity_names=["Gold"])

        batches = form_batches([s1, s2, s3], force=True)
        assert len(batches) == 3

    def test_large_group_split_into_windows(self):
        signals = [
            _make_signal(f"Signal {i}.", entity_names=["Crude Oil"])
            for i in range(MAX_BATCH_SIZE + 5)
        ]
        batches = form_batches(signals, force=True)
        assert len(batches) == 2
        assert all(len(b.signals) <= MAX_BATCH_SIZE for b in batches)

    def test_min_batch_size_filter(self):
        s1 = _make_signal("Signal A.", entity_names=["Entity A"])
        batches = form_batches([s1], force=False)
        assert batches == []

    def test_force_bypasses_min_batch_size(self):
        s1 = _make_signal("Signal A.", entity_names=["Entity A"])
        batches = form_batches([s1], force=True)
        assert len(batches) == 1

    def test_no_anchor_signals_grouped_separately(self):
        # Signals without proposed_anchors have empty entity sets → each gets own group
        s1 = _make_signal("No anchors 1.", entity_names=[])
        s2 = _make_signal("No anchors 2.", entity_names=[])
        batches = form_batches([s1, s2], force=True)
        # Both have empty entity sets; union-find won't merge them
        assert len(batches) == 2

    def test_transitivity(self):
        # A shares entity with B, B shares different entity with C → all in one group
        s_a = _make_signal("A.", entity_names=["Oil"])
        s_b = _make_signal("B.", entity_names=["Oil", "OPEC"])
        s_c = _make_signal("C.", entity_names=["OPEC", "Russia"])

        batches = form_batches([s_a, s_b, s_c], force=True)
        assert len(batches) == 1
        assert len(batches[0].signals) == 3

    def test_lens_ids_collected(self):
        s1 = _make_signal("Oil.", lens_id="commodities", entity_names=["Oil"])
        s2 = _make_signal("More oil.", lens_id="geopolitics", entity_names=["Oil"])
        batches = form_batches([s1, s2], force=True)
        assert len(batches) == 1
        assert batches[0].lens_ids == frozenset({"commodities", "geopolitics"})


# ═══════════════════════════════════════════════════════════════════════════════
# 2. AnchorBatch helpers
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnchorBatch:
    def test_signal_ids_extracted(self):
        s1 = _make_signal()
        s2 = _make_signal()
        batch = AnchorBatch(signals=[s1, s2])
        assert set(batch.signal_ids) == {s1["signal_id"], s2["signal_id"]}

    def test_content_timestamp_is_min(self):
        ts_early = datetime(2024, 1, 1, tzinfo=timezone.utc)
        ts_late = datetime(2024, 6, 1, tzinfo=timezone.utc)
        s1 = _make_signal(content_timestamp=ts_early)
        s2 = _make_signal(content_timestamp=ts_late)
        batch = AnchorBatch(signals=[s1, s2])
        assert batch.content_timestamp == ts_early

    def test_content_timestamp_falls_back_to_formed_at(self):
        sig = {"signal_id": uuid.uuid4(), "claim_text": "x", "content_timestamp": None}
        batch = AnchorBatch(signals=[sig])
        assert batch.content_timestamp == batch.formed_at


# ═══════════════════════════════════════════════════════════════════════════════
# 3. _parse_anchor_operations
# ═══════════════════════════════════════════════════════════════════════════════


_VALID_CREATE_NODE = {
    "operation": "create_node",
    "node_type": "entity",
    "proposed_id": "crude_oil",
    "fields": {"name": "Crude Oil", "entity_kind": "commodity"},
    "reasoning": "Primary commodity.",
}

_VALID_CREATE_EDGE = {
    "operation": "create_edge",
    "source_node_id": "crude_oil",
    "target_node_id": "opec",
    "edge_type": "causes",
    "proposed_weight_band": "provisional",
    "reasoning": "OPEC controls crude oil production.",
    "falsification_criteria": "If non-OPEC supply dominates pricing.",
}


class TestParseAnchorOperations:
    def test_valid_operations_parsed(self):
        content = json.dumps([_VALID_CREATE_NODE, _VALID_CREATE_EDGE])
        ops, err = _parse_anchor_operations(content, batch_id="test")
        assert len(ops) == 2
        assert err is None

    def test_empty_array_returns_empty(self):
        ops, err = _parse_anchor_operations("[]", batch_id="test")
        assert ops == []
        assert err is None

    def test_markdown_fenced_json_parsed(self):
        content = "```json\n" + json.dumps([_VALID_CREATE_NODE]) + "\n```"
        ops, err = _parse_anchor_operations(content, batch_id="test")
        assert len(ops) == 1
        assert err is None

    def test_malformed_json_returns_empty_with_error(self):
        ops, err = _parse_anchor_operations("Not JSON at all.", batch_id="test")
        assert ops == []
        assert err is not None

    def test_partial_valid_returns_valid_items(self):
        bad_item = {"operation": "create_node", "node_type": "invalid_type"}
        content = json.dumps([_VALID_CREATE_NODE, bad_item])
        ops, err = _parse_anchor_operations(content, batch_id="test")
        assert len(ops) == 1
        assert err is not None
        assert "skipped" in err

    def test_update_node_parsed(self):
        op = {
            "operation": "update_node",
            "target_node_id": str(uuid.uuid4()),
            "field_updates": {"description": "Updated."},
            "reasoning": "New info.",
        }
        ops, err = _parse_anchor_operations(json.dumps([op]), batch_id="test")
        assert len(ops) == 1

    def test_update_edge_weight_parsed(self):
        op = {
            "operation": "update_edge_weight",
            "target_edge_id": str(uuid.uuid4()),
            "new_weight_band": "moderate",
            "direction": "strengthen",
            "reasoning": "More evidence.",
        }
        ops, err = _parse_anchor_operations(json.dumps([op]), batch_id="test")
        assert len(ops) == 1

    def test_add_supporting_signal_parsed(self):
        op = {
            "operation": "add_supporting_signal",
            "target_edge_id": str(uuid.uuid4()),
            "signal_id": str(uuid.uuid4()),
        }
        ops, err = _parse_anchor_operations(json.dumps([op]), batch_id="test")
        assert len(ops) == 1

    def test_non_list_returns_error(self):
        ops, err = _parse_anchor_operations(json.dumps(_VALID_CREATE_NODE), batch_id="test")
        assert ops == []
        assert err is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. _extract_json_array
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractJsonArray:
    def test_plain_array(self):
        result = _extract_json_array('[{"a": 1}]')
        assert result == [{"a": 1}]

    def test_markdown_fenced(self):
        result = _extract_json_array("```json\n[{\"a\": 1}]\n```")
        assert result == [{"a": 1}]

    def test_embedded_in_prose(self):
        result = _extract_json_array('Here is the result:\n[{"a": 1}]\nDone.')
        assert result == [{"a": 1}]

    def test_not_json_returns_none(self):
        result = _extract_json_array("This is not JSON.")
        assert result is None

    def test_object_returns_none(self):
        result = _extract_json_array('{"key": "value"}')
        assert result is None

    def test_empty_array(self):
        result = _extract_json_array("[]")
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# 5. _render_subgraph_context
# ═══════════════════════════════════════════════════════════════════════════════


class TestRenderSubgraphContext:
    def _make_node(self, name: str, node_type: str = "entity"):
        node = MagicMock()
        node.node_id = uuid.uuid4()
        node.name = name
        node.node_type = node_type
        node.description = f"Description of {name}"
        return node

    def _make_edge(self, source_id, target_id, edge_type: str = "causes"):
        edge = MagicMock()
        edge.edge_id = uuid.uuid4()
        edge.source_node_id = source_id
        edge.target_node_id = target_id
        edge.edge_type = edge_type
        edge.current_weight_band = "moderate"
        edge.reasoning = "Some reasoning."
        edge.falsification_criteria = "Some criterion."
        return edge

    def test_renders_nodes_and_edges(self):
        node_a = self._make_node("Crude Oil")
        node_b = self._make_node("OPEC")
        edge = self._make_edge(node_a.node_id, node_b.node_id)

        result = _render_subgraph_context([node_a, node_b], [edge], char_budget=10000)
        assert "Crude Oil" in result
        assert "OPEC" in result
        assert "causes" in result

    def test_empty_subgraph(self):
        result = _render_subgraph_context([], [], char_budget=10000)
        assert "0 nodes" in result

    def test_char_budget_respected(self):
        nodes = [self._make_node(f"Entity {i}") for i in range(100)]
        result = _render_subgraph_context(nodes, [], char_budget=500)
        assert len(result) < 2000  # some truncation occurred


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Prompt builders
# ═══════════════════════════════════════════════════════════════════════════════


class TestPromptBuilders:
    def test_build_system_prompt_contains_role_and_schema(self):
        prompt = build_system_prompt()
        assert "anchoring stage" in prompt
        assert "Graph schema" in prompt
        assert "create_node" in prompt
        assert "falsification_criteria" in prompt

    def test_build_user_message_contains_sections(self):
        signals = [_make_signal("Oil rose.")]
        msg = build_user_message(
            subgraph_context="## Subgraph\n\n(empty)",
            signal_batch=signals,
        )
        assert "Current subgraph context" in msg
        assert "Signal batch" in msg
        assert "Oil rose." in msg
        assert "OUTPUT" in msg or "task" in msg.lower()

    def test_build_user_message_signal_count(self):
        signals = [_make_signal(f"Claim {i}.") for i in range(5)]
        msg = build_user_message(
            subgraph_context="(none)",
            signal_batch=signals,
        )
        assert "5 signal(s)" in msg

    def test_build_user_message_includes_proposed_anchors(self):
        sig = _make_signal("Crude fell.", entity_names=["Crude Oil"])
        msg = build_user_message(
            subgraph_context="(none)",
            signal_batch=[sig],
        )
        assert "create_node" in msg
        assert "Crude Oil" in msg


# ═══════════════════════════════════════════════════════════════════════════════
# 7. AnchoringOrchestrator.run_batch — mock integration
# ═══════════════════════════════════════════════════════════════════════════════


def _make_mock_pool() -> MagicMock:
    pool = MagicMock()
    conn = AsyncMock()
    txn = AsyncMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_mock_llm(content: str = "[]") -> MagicMock:
    response = MagicMock()
    response.content = content
    response.model = "test-model"
    response.prompt_tokens = 200
    response.completion_tokens = 50
    response.langfuse_trace_id = "trace-123"

    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=response)
    return llm


class TestAnchoringOrchestratorRunBatch:
    def _make_batch(self, signals=None) -> AnchorBatch:
        if signals is None:
            signals = [_make_signal("Oil fell.", entity_names=["Crude Oil"])]
        return AnchorBatch(signals=signals, lens_ids=frozenset({"commodities"}))

    @pytest.mark.asyncio
    async def test_empty_llm_response_marks_anchored(self):
        pool = _make_mock_pool()
        llm = _make_mock_llm("[]")

        orchestrator = AnchoringOrchestrator(pool, llm)

        # Mock subgraph and tier_a
        orchestrator._reader = AsyncMock()
        orchestrator._reader.search_nodes = AsyncMock(return_value=[])
        orchestrator._tier_a = AsyncMock()
        orchestrator._tier_a.mark_anchored = AsyncMock()

        batch = self._make_batch()
        result = await orchestrator.run_batch(batch)

        assert result.n_signals == 1
        assert result.n_applied == 0
        assert result.llm_error is None
        orchestrator._tier_a.mark_anchored.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_error_captured(self):
        from augur.llm.client import LLMCallError

        pool = _make_mock_pool()
        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=LLMCallError("timeout"))

        orchestrator = AnchoringOrchestrator(pool, llm)
        orchestrator._reader = AsyncMock()
        orchestrator._reader.search_nodes = AsyncMock(return_value=[])

        batch = self._make_batch()
        result = await orchestrator.run_batch(batch)

        assert result.llm_error is not None
        assert "timeout" in result.llm_error
        assert result.n_applied == 0

    @pytest.mark.asyncio
    async def test_valid_operations_passed_to_applier(self):
        llm_output = json.dumps([_VALID_CREATE_NODE])

        pool = _make_mock_pool()
        llm = _make_mock_llm(llm_output)

        from augur.graph.models import ApplierResult, GraphUpdateEvent
        dummy_event = GraphUpdateEvent(
            event_type="create_node",
            operation_data={},
            content_timestamp=_TS,
        )
        mock_result = ApplierResult(applied=[dummy_event], rejected=[])

        orchestrator = AnchoringOrchestrator(pool, llm)
        orchestrator._reader = AsyncMock()
        orchestrator._reader.search_nodes = AsyncMock(return_value=[])
        orchestrator._applier = AsyncMock()
        orchestrator._applier.apply = AsyncMock(return_value=mock_result)
        orchestrator._tier_a = AsyncMock()
        orchestrator._tier_a.mark_anchored = AsyncMock()

        batch = self._make_batch()
        result = await orchestrator.run_batch(batch)

        orchestrator._applier.apply.assert_called_once()
        assert result.n_applied == 1
        assert result.n_rejected == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 8. AnchoringOrchestrator.run_cycle — no signals path
# ═══════════════════════════════════════════════════════════════════════════════


class TestAnchoringOrchestratorRunCycle:
    @pytest.mark.asyncio
    async def test_no_signals_returns_empty_cycle(self):
        pool = _make_mock_pool()
        llm = _make_mock_llm()

        orchestrator = AnchoringOrchestrator(pool, llm)
        orchestrator._tier_a = AsyncMock()
        orchestrator._tier_a.get_unanchored = AsyncMock(return_value=[])

        result = await orchestrator.run_cycle()

        assert result.n_batches == 0
        assert result.n_signals_processed == 0
        assert result.finished_at is not None

    @pytest.mark.asyncio
    async def test_cycle_processes_all_batches(self):
        signals = [
            _make_signal("Oil up.", entity_names=["Crude Oil"]),
            _make_signal("Oil supply cut.", entity_names=["Crude Oil"]),
        ]

        pool = _make_mock_pool()
        llm = _make_mock_llm("[]")

        orchestrator = AnchoringOrchestrator(pool, llm)
        orchestrator._tier_a = AsyncMock()
        orchestrator._tier_a.get_unanchored = AsyncMock(return_value=signals)
        orchestrator._tier_a.mark_anchored = AsyncMock()
        orchestrator._reader = AsyncMock()
        orchestrator._reader.search_nodes = AsyncMock(return_value=[])

        result = await orchestrator.run_cycle(force=True)

        # Both signals share "crude oil" → 1 batch
        assert result.n_batches == 1
        assert result.n_signals_processed == 2
