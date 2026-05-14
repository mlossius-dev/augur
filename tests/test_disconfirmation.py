"""
Unit tests for the Phase 5 disconfirmation pass.

Tests:
  1. challenger: build_challenge_prompt — content checks
  2. challenger: parse_challenge_output — valid found/not_found, malformed, edge cases
  3. challenger: one_step_weaker — band ordering
  4. orchestrator: _parse_operations — valid ops, invalid op types filtered
  5. DisconfirmationOrchestrator: run_pass — no edges path
  6. DisconfirmationOrchestrator: _challenge_edge — found/not_found/LLM error paths
  7. selector: select_edges SQL query shape (mocked pool)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from augur.disconfirmation.challenger import (
    build_challenge_prompt,
    one_step_weaker,
    parse_challenge_output,
)
from augur.disconfirmation.orchestrator import (
    DisconfirmationOrchestrator,
    EdgeChallengeResult,
    _parse_operations,
)

_TS = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_EDGE_ID = uuid.uuid4()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _make_edge(weight: str = "strong") -> dict[str, Any]:
    return {
        "edge_id": _EDGE_ID,
        "source_node_id": uuid.uuid4(),
        "target_node_id": uuid.uuid4(),
        "edge_type": "causes",
        "current_weight_band": weight,
        "reasoning": "High gas prices cause ammonia production curtailment.",
        "falsification_criteria": (
            "If ammonia production volumes remain stable despite high gas prices "
            "for three or more consecutive months."
        ),
        "source_name": "European Natural Gas Price",
        "target_name": "Ammonia Production",
        "supporting_signals": [uuid.uuid4(), uuid.uuid4()],
        "disconfirming_signals": [],
        "last_disconfirmation_pass": None,
        "created_at": _TS,
    }


def _make_signal(claim: str = "Production steady.", confidence: str = "reported_claim") -> dict[str, Any]:
    return {
        "signal_id": uuid.uuid4(),
        "lens_id": "commodities",
        "claim_text": claim,
        "confidence_band": confidence,
        "reasoning": "Reuters report.",
        "content_timestamp": _TS,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 1. build_challenge_prompt
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuildChallengePrompt:
    def test_includes_edge_details(self):
        edge = _make_edge()
        prompt = build_challenge_prompt(edge, [])
        assert "Ammonia Production" in prompt
        assert "European Natural Gas Price" in prompt
        assert str(_EDGE_ID) in prompt
        assert "falsification_criteria" in prompt.lower() or "Falsification" in prompt

    def test_includes_signals(self):
        edge = _make_edge()
        signals = [_make_signal("Production volumes up 2%.")]
        prompt = build_challenge_prompt(edge, signals)
        assert "Production volumes up 2%." in prompt

    def test_no_signals_placeholder(self):
        edge = _make_edge()
        prompt = build_challenge_prompt(edge, [])
        assert "no recent signals" in prompt.lower()

    def test_multiple_signals_numbered(self):
        edge = _make_edge()
        signals = [_make_signal(f"Claim {i}.") for i in range(3)]
        prompt = build_challenge_prompt(edge, signals)
        assert "Signal 1" in prompt
        assert "Signal 3" in prompt

    def test_includes_task_instruction(self):
        edge = _make_edge()
        prompt = build_challenge_prompt(edge, [])
        assert "falsification criteria" in prompt.lower()
        assert "JSON" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 2. parse_challenge_output
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseChallengeOutput:
    def test_parse_found_outcome(self):
        content = json.dumps({
            "outcome": "found",
            "reasoning": "Signal X directly meets the falsification criteria.",
            "operations": [
                {"operation": "add_disconfirming_signal",
                 "target_edge_id": str(_EDGE_ID),
                 "signal_id": str(uuid.uuid4())},
            ],
        })
        result = parse_challenge_output(content, edge_id=str(_EDGE_ID))
        assert result["outcome"] == "found"
        assert len(result["operations"]) == 1
        assert result["reasoning"] != ""

    def test_parse_not_found_outcome(self):
        content = json.dumps({
            "outcome": "not_found",
            "reasoning": "No evidence in the window meets the criteria.",
        })
        result = parse_challenge_output(content, edge_id=str(_EDGE_ID))
        assert result["outcome"] == "not_found"
        assert result["operations"] == []

    def test_parse_markdown_fenced(self):
        inner = json.dumps({"outcome": "not_found", "reasoning": "Nothing found."})
        content = f"```json\n{inner}\n```"
        result = parse_challenge_output(content, edge_id=str(_EDGE_ID))
        assert result["outcome"] == "not_found"

    def test_parse_malformed_returns_error(self):
        result = parse_challenge_output("Not valid JSON.", edge_id=str(_EDGE_ID))
        assert result["outcome"] == "error"

    def test_parse_invalid_outcome_returns_error(self):
        content = json.dumps({"outcome": "maybe", "reasoning": "Unsure."})
        result = parse_challenge_output(content, edge_id=str(_EDGE_ID))
        assert result["outcome"] == "error"

    def test_parse_extracts_embedded_json_object(self):
        content = "Some preamble.\n" + json.dumps({
            "outcome": "not_found",
            "reasoning": "Nothing.",
        }) + "\nSome trailing text."
        result = parse_challenge_output(content, edge_id=str(_EDGE_ID))
        assert result["outcome"] == "not_found"

    def test_not_found_operations_empty(self):
        content = json.dumps({
            "outcome": "not_found",
            "reasoning": "No evidence.",
            "operations": [{"operation": "update_edge_weight"}],
        })
        result = parse_challenge_output(content, edge_id=str(_EDGE_ID))
        assert result["operations"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. one_step_weaker
# ═══════════════════════════════════════════════════════════════════════════════


class TestOneStepWeaker:
    def test_strong_to_moderate(self):
        assert one_step_weaker("strong") == "moderate"

    def test_moderate_to_weak(self):
        assert one_step_weaker("moderate") == "weak"

    def test_weak_to_provisional(self):
        assert one_step_weaker("weak") == "provisional"

    def test_provisional_to_disputed(self):
        assert one_step_weaker("provisional") == "disputed"

    def test_disputed_returns_none(self):
        assert one_step_weaker("disputed") is None

    def test_unknown_returns_none(self):
        assert one_step_weaker("nonexistent") is None


# ═══════════════════════════════════════════════════════════════════════════════
# 4. _parse_operations — valid and invalid
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseOperations:
    def test_valid_add_disconfirming_signal(self):
        ops = [
            {"operation": "add_disconfirming_signal",
             "target_edge_id": str(uuid.uuid4()),
             "signal_id": str(uuid.uuid4())},
        ]
        result = _parse_operations(ops, edge_id="test")
        assert len(result) == 1

    def test_valid_update_edge_weight(self):
        ops = [
            {"operation": "update_edge_weight",
             "target_edge_id": str(uuid.uuid4()),
             "new_weight_band": "weak",
             "direction": "weaken",
             "reasoning": "Evidence meets criteria."},
        ]
        result = _parse_operations(ops, edge_id="test")
        assert len(result) == 1

    def test_invalid_op_type_filtered(self):
        ops = [
            {"operation": "create_node", "node_type": "entity",
             "proposed_id": "x", "fields": {"name": "X"}, "reasoning": "bad"},
        ]
        result = _parse_operations(ops, edge_id="test")
        assert result == []

    def test_mixed_valid_and_invalid(self):
        ops = [
            {"operation": "add_disconfirming_signal",
             "target_edge_id": str(uuid.uuid4()),
             "signal_id": str(uuid.uuid4())},
            {"operation": "create_node", "node_type": "entity"},  # invalid type
        ]
        result = _parse_operations(ops, edge_id="test")
        assert len(result) == 1

    def test_non_dict_items_skipped(self):
        result = _parse_operations(["not_a_dict", 42, None], edge_id="test")
        assert result == []

    def test_add_supporting_signal_filtered(self):
        ops = [
            {"operation": "add_supporting_signal",
             "target_edge_id": str(uuid.uuid4()),
             "signal_id": str(uuid.uuid4())},
        ]
        result = _parse_operations(ops, edge_id="test")
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# 5. DisconfirmationOrchestrator.run_pass — no edges path
# ═══════════════════════════════════════════════════════════════════════════════


def _make_pool_mock(fetch_returns=None):
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=fetch_returns or [])
    conn.execute = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_llm_mock(outcome: str = "not_found", reasoning: str = "No evidence found."):
    response = MagicMock()
    response.content = json.dumps({"outcome": outcome, "reasoning": reasoning, "operations": []})
    response.model = "claude-opus-4"
    response.prompt_tokens = 500
    response.completion_tokens = 100
    response.langfuse_trace_id = "trace-disconf-001"

    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=response)
    return llm


class TestRunPassNoEdges:
    @pytest.mark.asyncio
    async def test_no_edges_returns_empty_result(self):
        pool = _make_pool_mock(fetch_returns=[])
        llm = _make_llm_mock()

        orchestrator = DisconfirmationOrchestrator(pool, llm)
        # Patch select_edges to return empty
        from unittest.mock import patch
        with patch(
            "augur.disconfirmation.orchestrator.select_edges",
            new=AsyncMock(return_value=[]),
        ):
            result = await orchestrator.run_pass()

        assert result.n_edges_challenged == 0
        assert result.n_found == 0
        assert result.n_not_found == 0
        assert result.finished_at is not None

    @pytest.mark.asyncio
    async def test_finish_sets_timestamps(self):
        pool = _make_pool_mock(fetch_returns=[])
        llm = _make_llm_mock()
        orchestrator = DisconfirmationOrchestrator(pool, llm)

        from unittest.mock import patch
        with patch(
            "augur.disconfirmation.orchestrator.select_edges",
            new=AsyncMock(return_value=[]),
        ):
            result = await orchestrator.run_pass()

        assert result.started_at <= result.finished_at


# ═══════════════════════════════════════════════════════════════════════════════
# 6. DisconfirmationOrchestrator._challenge_edge
# ═══════════════════════════════════════════════════════════════════════════════


class TestChallengeEdge:
    def _make_orchestrator(self, llm_content: str) -> DisconfirmationOrchestrator:
        pool = _make_pool_mock()
        response = MagicMock()
        response.content = llm_content
        response.langfuse_trace_id = "trace-x"

        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=response)

        orch = DisconfirmationOrchestrator(pool, llm)
        # Patch load_recent_signals_for_edge
        from unittest.mock import patch
        self._patch = patch(
            "augur.disconfirmation.orchestrator.load_recent_signals_for_edge",
            new=AsyncMock(return_value=[_make_signal("Production stable.")]),
        )
        self._patch.start()
        return orch

    def teardown_method(self, _):
        if hasattr(self, "_patch"):
            self._patch.stop()

    @pytest.mark.asyncio
    async def test_not_found_outcome(self):
        content = json.dumps({"outcome": "not_found", "reasoning": "No evidence."})
        orch = self._make_orchestrator(content)
        edge = _make_edge()

        result = await orch._challenge_edge(edge, signal_window_days=7)

        assert result.outcome == "not_found"
        assert result.n_operations_applied == 0

    @pytest.mark.asyncio
    async def test_llm_error_captured(self):
        from augur.llm.client import LLMCallError

        pool = _make_pool_mock()
        llm = AsyncMock()
        llm.complete = AsyncMock(side_effect=LLMCallError("timeout"))

        from unittest.mock import patch
        with patch(
            "augur.disconfirmation.orchestrator.load_recent_signals_for_edge",
            new=AsyncMock(return_value=[]),
        ):
            orch = DisconfirmationOrchestrator(pool, llm)
            result = await orch._challenge_edge(_make_edge(), signal_window_days=7)

        assert result.outcome == "error"
        assert result.llm_error is not None
        assert "timeout" in result.llm_error

    @pytest.mark.asyncio
    async def test_found_with_operations_calls_applier(self):
        sig_id = str(uuid.uuid4())
        edge_id = str(_EDGE_ID)

        content = json.dumps({
            "outcome": "found",
            "reasoning": "Ammonia production data contradicts edge.",
            "operations": [
                {"operation": "add_disconfirming_signal",
                 "target_edge_id": edge_id,
                 "signal_id": sig_id},
            ],
        })

        pool = _make_pool_mock()
        response = MagicMock()
        response.content = content
        response.langfuse_trace_id = "trace-found"

        llm = AsyncMock()
        llm.complete = AsyncMock(return_value=response)

        from augur.graph.models import ApplierResult, GraphUpdateEvent
        dummy_event = GraphUpdateEvent(
            event_type="add_disconfirming_signal",
            operation_data={},
            content_timestamp=_TS,
        )
        mock_applier_result = ApplierResult(applied=[dummy_event], rejected=[])

        from unittest.mock import patch, AsyncMock as AM
        with patch(
            "augur.disconfirmation.orchestrator.load_recent_signals_for_edge",
            new=AsyncMock(return_value=[_make_signal()]),
        ), patch.object(
            DisconfirmationOrchestrator,
            "_apply_operations",
            new=AM(return_value=mock_applier_result),
        ) if False else patch(
            "augur.disconfirmation.orchestrator.Applier",
        ) as mock_applier_cls:
            mock_applier = AsyncMock()
            mock_applier.apply = AsyncMock(return_value=mock_applier_result)
            mock_applier_cls.return_value = mock_applier

            orch = DisconfirmationOrchestrator(pool, llm)
            orch._applier = mock_applier

            result = await orch._challenge_edge(_make_edge(), signal_window_days=7)

        assert result.outcome == "found"
        mock_applier.apply.assert_called_once()
        assert result.n_operations_applied == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 7. EdgeChallengeResult dataclass
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeChallengeResult:
    def test_defaults(self):
        er = EdgeChallengeResult(
            edge_id=_EDGE_ID,
            outcome="not_found",
            reasoning="No evidence.",
        )
        assert er.n_operations_applied == 0
        assert er.n_operations_rejected == 0
        assert er.llm_error is None
        assert er.langfuse_trace_id is None
