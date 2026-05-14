"""
Disconfirmation pass orchestrator.

Drives one full periodic disconfirmation cycle:

  1. Select edges to challenge (EdgeSelector).
  2. For each edge:
     a. Load recent Tier A signals adjacent to the edge's neighbourhood.
     b. Build the challenge prompt.
     c. Call the LLM (strong model via PipelineStage.DISCONFIRMATION).
     d. Parse the output.
     e. If disconfirmation found → parse operations → run through Applier.
     f. Record outcome in disconfirmation_pass_events.
     g. Update edges.last_disconfirmation_pass.
  3. Return a DisconfirmationPassResult.

Architecture invariant: the Applier is the only write gate.  The orchestrator
never writes to the graph directly.  Pass event records (including
no_disconfirmation_found) are written directly to disconfirmation_pass_events
since they are not graph mutations.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from pydantic import TypeAdapter, ValidationError

from augur.disconfirmation.challenger import (
    _CHALLENGE_SYSTEM_PROMPT,
    build_challenge_prompt,
    parse_challenge_output,
)
from augur.disconfirmation.selector import (
    load_recent_signals_for_edge,
    select_edges,
)
from augur.graph.applier import Applier
from augur.graph.models import ProposedAnchorOperation
from augur.llm.client import LLMCallError
from augur.llm.models import PipelineStage

log = structlog.get_logger(__name__)

_OP_ADAPTER: TypeAdapter[ProposedAnchorOperation] = TypeAdapter(ProposedAnchorOperation)


# ── Result models ─────────────────────────────────────────────────────────────


@dataclass
class EdgeChallengeResult:
    """Result of challenging a single edge."""

    edge_id: UUID
    outcome: str  # found | not_found | error
    reasoning: str
    n_operations_applied: int = 0
    n_operations_rejected: int = 0
    llm_error: str | None = None
    langfuse_trace_id: str | None = None


@dataclass
class DisconfirmationPassResult:
    """Aggregate result of one disconfirmation pass."""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    n_edges_selected: int = 0
    n_edges_challenged: int = 0
    n_found: int = 0
    n_not_found: int = 0
    n_error: int = 0
    n_operations_applied: int = 0
    n_operations_rejected: int = 0
    edge_results: list[EdgeChallengeResult] = field(default_factory=list)

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc)
        self.n_edges_challenged = len(self.edge_results)
        self.n_found = sum(1 for r in self.edge_results if r.outcome == "found")
        self.n_not_found = sum(1 for r in self.edge_results if r.outcome == "not_found")
        self.n_error = sum(1 for r in self.edge_results if r.outcome == "error")
        self.n_operations_applied = sum(r.n_operations_applied for r in self.edge_results)
        self.n_operations_rejected = sum(r.n_operations_rejected for r in self.edge_results)


# ── Orchestrator ─────────────────────────────────────────────────────────────


class DisconfirmationOrchestrator:
    """
    Drives the periodic disconfirmation pass.

    Instantiate once; reuse across scheduled runs.
    """

    def __init__(self, pool: asyncpg.Pool, llm_client: Any) -> None:
        self._pool = pool
        self._llm = llm_client
        self._applier = Applier(pool)

    async def run_pass(
        self,
        *,
        limit: int = 20,
        rechallenge_days: int = 7,
        stale_signal_days: int = 30,
        signal_window_days: int = 7,
    ) -> DisconfirmationPassResult:
        """
        Run one full disconfirmation pass.

        Args:
            limit: Maximum edges to challenge per pass.
            rechallenge_days: Don't re-challenge edges challenged within N days.
            stale_signal_days: Flag edges whose supporting signals are this old.
            signal_window_days: Look back this many days for Tier A signals.
        """
        result = DisconfirmationPassResult()

        edges = await select_edges(
            self._pool,
            limit=limit,
            rechallenge_days=rechallenge_days,
            stale_signal_days=stale_signal_days,
        )
        result.n_edges_selected = len(edges)

        log.info(
            "disconfirmation.pass_start",
            n_edges=len(edges),
            limit=limit,
        )

        for edge in edges:
            er = await self._challenge_edge(
                edge,
                signal_window_days=signal_window_days,
            )
            result.edge_results.append(er)

        result.finish()
        log.info(
            "disconfirmation.pass_complete",
            n_found=result.n_found,
            n_not_found=result.n_not_found,
            n_error=result.n_error,
            n_applied=result.n_operations_applied,
        )
        return result

    async def _challenge_edge(
        self,
        edge: dict[str, Any],
        *,
        signal_window_days: int,
    ) -> EdgeChallengeResult:
        """Challenge a single edge and record the outcome."""
        edge_id = edge["edge_id"]
        er = EdgeChallengeResult(edge_id=edge_id, outcome="error", reasoning="")

        log.info(
            "disconfirmation.challenge_start",
            edge_id=str(edge_id),
            source=edge.get("source_name"),
            target=edge.get("target_name"),
            weight=edge.get("current_weight_band"),
        )

        # Load recent adjacent signals from Tier A
        recent_signals = await load_recent_signals_for_edge(
            self._pool,
            edge=edge,
            window_days=signal_window_days,
        )

        # Build prompt and call LLM
        user_message = build_challenge_prompt(edge, recent_signals)
        signal_ids = [s["signal_id"] for s in recent_signals]

        try:
            response = await self._llm.complete(
                stage=PipelineStage.DISCONFIRMATION,
                prompt_template_id="disconfirmation_pass_v1",
                messages=[
                    {"role": "system", "content": _CHALLENGE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                metadata={
                    "edge_id": str(edge_id),
                    "source_name": edge.get("source_name"),
                    "target_name": edge.get("target_name"),
                    "weight_band": edge.get("current_weight_band"),
                },
            )
        except LLMCallError as exc:
            er.llm_error = str(exc)
            er.outcome = "error"
            er.reasoning = f"LLM call failed: {exc}"
            await self._record_pass_event(edge, er, signal_ids, None)
            return er

        er.langfuse_trace_id = response.langfuse_trace_id

        # Parse output
        parsed = parse_challenge_output(response.content, edge_id=str(edge_id))
        er.outcome = parsed["outcome"]
        er.reasoning = parsed["reasoning"]

        if parsed["outcome"] == "found" and parsed["operations"]:
            # Validate and apply operations
            ops = _parse_operations(parsed["operations"], edge_id=str(edge_id))
            if ops:
                applier_result = await self._applier.apply(
                    ops,
                    content_timestamp=datetime.now(timezone.utc),
                    source="disconfirmation",
                    triggered_by=signal_ids[:10],  # asyncpg accepts list of UUIDs
                    langfuse_trace_ids=[response.langfuse_trace_id],
                )
                er.n_operations_applied = len(applier_result.applied)
                er.n_operations_rejected = len(applier_result.rejected)

        # Record the pass event and update last_disconfirmation_pass
        await self._record_pass_event(edge, er, signal_ids, response.langfuse_trace_id)
        await self._mark_challenged(edge_id)

        log.info(
            "disconfirmation.challenge_complete",
            edge_id=str(edge_id),
            outcome=er.outcome,
            n_applied=er.n_operations_applied,
        )
        return er

    async def _record_pass_event(
        self,
        edge: dict[str, Any],
        result: EdgeChallengeResult,
        signal_ids: list[UUID],
        trace_id: str | None,
    ) -> None:
        """Insert a disconfirmation_pass_events record."""
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO disconfirmation_pass_events
                        (pass_event_id, edge_id, challenged_at, outcome,
                         reasoning, signals_reviewed, langfuse_trace_id,
                         weight_band_at_challenge)
                    VALUES ($1, $2, now(), $3, $4, $5, $6, $7)
                    """,
                    uuid.uuid4(),
                    result.edge_id,
                    result.outcome,
                    result.reasoning,
                    signal_ids,
                    trace_id,
                    str(edge.get("current_weight_band", "")),
                )
            except Exception as exc:
                log.error(
                    "disconfirmation.record_failed",
                    edge_id=str(result.edge_id),
                    error=str(exc),
                )

    async def _mark_challenged(self, edge_id: UUID) -> None:
        """Update edges.last_disconfirmation_pass = now()."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE edges SET last_disconfirmation_pass = now() WHERE edge_id = $1",
                edge_id,
            )


# ── Operation parsing ─────────────────────────────────────────────────────────


def _parse_operations(
    raw_ops: list[Any], *, edge_id: str
) -> list[ProposedAnchorOperation]:
    """Parse a list of raw operation dicts into ProposedAnchorOperation models."""
    valid: list[ProposedAnchorOperation] = []
    for item in raw_ops:
        if not isinstance(item, dict):
            continue
        # Disconfirmation pass can only produce these two operation types
        op_type = item.get("operation")
        if op_type not in ("add_disconfirming_signal", "update_edge_weight"):
            log.warning(
                "disconfirmation.invalid_op_type",
                edge_id=edge_id,
                op_type=op_type,
            )
            continue
        try:
            valid.append(_OP_ADAPTER.validate_python(item))
        except (ValidationError, Exception) as exc:
            log.warning(
                "disconfirmation.op_parse_failed",
                edge_id=edge_id,
                error=str(exc),
                item=str(item)[:200],
            )
    return valid
