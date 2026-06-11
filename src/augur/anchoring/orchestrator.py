"""
Anchoring orchestrator.

Drives one anchoring cycle:

  1. Pull unanchored signals from Tier A (via TierAStore).
  2. Form topically-coherent batches (via batch_former).
  3. For each batch:
     a. Snapshot the relevant subgraph neighbourhood from Tier B (GraphReader).
     b. Render the anchoring prompt (prompt module).
     c. Call the LLM (LLMClient at PipelineStage.ANCHORING).
     d. Parse LLM output → list[ProposedAnchorOperation].
     e. Pass operations to the Applier.
     f. Mark signals as anchored in Tier A.
  4. Return a summary of all applied/rejected events across the cycle.

Architecture invariant: the Applier is the only write gate.  The orchestrator
never writes to the graph directly — it only calls Applier.apply().
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg
import structlog
from pydantic import TypeAdapter, ValidationError

from augur.anchoring.batch_former import AnchorBatch, form_batches
from augur.anchoring.prompt import build_system_prompt, build_user_message
from augur.extraction.tier_a import TierAStore
from augur.graph.applier import Applier
from augur.graph.models import ApplierResult, ProposedAnchorOperation
from augur.graph.reader import GraphReader
from augur.llm.client import LLMClient, LLMCallError
from augur.llm.models import PipelineStage

log = structlog.get_logger(__name__)

# TypeAdapter lets us validate a list of ProposedAnchorOperation in one call.
_OPERATIONS_ADAPTER: TypeAdapter[list[ProposedAnchorOperation]] = TypeAdapter(
    list[ProposedAnchorOperation]
)

# Maximum nodes whose subgraphs we expand for context; prevents very long prompts.
_MAX_CONTEXT_ROOTS = 6
# Subgraph depth per root node
_SUBGRAPH_DEPTH = 2
# Character budget for the subgraph context section of the prompt.
_SUBGRAPH_CHAR_BUDGET = 8_000


# ── Result model ─────────────────────────────────────────────────────────────


@dataclass
class BatchAnchoringResult:
    """Result of running one batch through the anchoring LLM + Applier."""

    batch_id: UUID
    signal_ids: list[UUID]
    n_signals: int
    n_applied: int = 0
    n_rejected: int = 0
    llm_error: str | None = None
    parse_error: str | None = None
    applier_result: ApplierResult | None = None


@dataclass
class AnchoringCycleResult:
    """Aggregate result of one full anchoring cycle."""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None
    n_batches: int = 0
    n_signals_processed: int = 0
    n_applied: int = 0
    n_rejected: int = 0
    batch_results: list[BatchAnchoringResult] = field(default_factory=list)

    def finish(self) -> None:
        self.finished_at = datetime.now(timezone.utc)
        self.n_batches = len(self.batch_results)
        self.n_signals_processed = sum(r.n_signals for r in self.batch_results)
        self.n_applied = sum(r.n_applied for r in self.batch_results)
        self.n_rejected = sum(r.n_rejected for r in self.batch_results)


# ── Orchestrator ─────────────────────────────────────────────────────────────


class AnchoringOrchestrator:
    """
    Coordinates the full anchoring pipeline.

    Instantiate once per application lifecycle; the pool, LLM client, and
    sub-objects are shared across calls.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        llm_client: LLMClient,
    ) -> None:
        self._pool = pool
        self._llm = llm_client
        self._tier_a = TierAStore(pool)
        self._reader = GraphReader(pool)
        self._applier = Applier(pool)

    # ── Public API ────────────────────────────────────────────────────────────

    async def run_cycle(
        self,
        *,
        lens_id: str | None = None,
        min_age_hours: int = 1,
        limit: int = 200,
        force: bool = False,
        max_hold_hours: float | None = 6.0,
    ) -> AnchoringCycleResult:
        """
        Run one full anchoring cycle.

        Pulls up to `limit` unanchored signals from Tier A, forms batches,
        and runs each batch through the LLM + Applier.

        Args:
            lens_id: Only process signals from this lens (None = all lenses).
            min_age_hours: Minimum signal age before anchoring.
            limit: Maximum signals to pull from Tier A per cycle.
            force: Pass to form_batches; includes under-MIN_BATCH_SIZE batches.
            max_hold_hours: Release sub-MIN_BATCH_SIZE batches whose oldest
                signal has waited this long, so a sparse / cold-start graph can
                bootstrap instead of holding singletons back forever. None
                disables the escape hatch (strict hold-back).
        """
        cycle = AnchoringCycleResult()

        signals = await self._tier_a.get_unanchored(
            lens_id=lens_id,
            min_age_hours=min_age_hours,
            limit=limit,
        )

        if not signals:
            log.info("anchoring.no_signals")
            cycle.finish()
            return cycle

        log.info("anchoring.signals_pulled", n=len(signals))
        batches = form_batches(signals, force=force, max_hold_hours=max_hold_hours)
        log.info("anchoring.batches_formed", n=len(batches))

        for batch in batches:
            result = await self.run_batch(batch)
            cycle.batch_results.append(result)

        cycle.finish()
        log.info(
            "anchoring.cycle_complete",
            n_batches=cycle.n_batches,
            n_signals=cycle.n_signals_processed,
            n_applied=cycle.n_applied,
            n_rejected=cycle.n_rejected,
        )
        return cycle

    async def run_batch(self, batch: AnchorBatch) -> BatchAnchoringResult:
        """
        Run one AnchorBatch through the full anchoring pipeline.

        Steps:
          1. Build subgraph context string.
          2. Build LLM prompt.
          3. Call LLM.
          4. Parse output → ProposedAnchorOperation list.
          5. Apply via Applier.
          6. Mark signals anchored.
        """
        result = BatchAnchoringResult(
            batch_id=batch.batch_id,
            signal_ids=batch.signal_ids,
            n_signals=len(batch.signals),
        )

        log.info(
            "anchoring.batch_start",
            batch_id=str(batch.batch_id),
            n_signals=len(batch.signals),
            lens_ids=list(batch.lens_ids),
        )

        # Step 1: build subgraph context
        subgraph_context = await self._build_subgraph_context(batch.signals)

        # Step 2: build messages
        system_prompt = build_system_prompt()
        user_message = build_user_message(
            subgraph_context=subgraph_context,
            signal_batch=batch.signals,
        )

        # Step 3: call LLM
        try:
            response = await self._llm.complete(
                stage=PipelineStage.ANCHORING,
                prompt_template_id="anchoring_v1",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                metadata={
                    "batch_id": str(batch.batch_id),
                    "n_signals": len(batch.signals),
                    "lens_ids": list(batch.lens_ids),
                },
            )
        except LLMCallError as exc:
            result.llm_error = str(exc)
            log.error("anchoring.llm_error", batch_id=str(batch.batch_id), error=str(exc))
            return result

        # Step 4: parse
        operations, parse_error = _parse_anchor_operations(
            response.content, batch_id=str(batch.batch_id)
        )
        if parse_error:
            result.parse_error = parse_error

        if not operations:
            log.info(
                "anchoring.no_operations",
                batch_id=str(batch.batch_id),
                parse_error=parse_error,
            )
            await self._tier_a.mark_anchored(batch.signal_ids)
            return result

        # Step 5: apply
        applier_result = await self._applier.apply(
            operations,
            content_timestamp=batch.content_timestamp,
            source="anchoring",
            triggered_by=batch.signal_ids,
            langfuse_trace_ids=[response.langfuse_trace_id],
        )
        result.applier_result = applier_result
        result.n_applied = len(applier_result.applied)
        result.n_rejected = len(applier_result.rejected)

        log.info(
            "anchoring.batch_applied",
            batch_id=str(batch.batch_id),
            n_applied=result.n_applied,
            n_rejected=result.n_rejected,
        )

        # Step 6: mark anchored
        await self._tier_a.mark_anchored(batch.signal_ids)

        return result

    # ── Subgraph context builder ──────────────────────────────────────────────

    async def _build_subgraph_context(
        self, signals: list[dict[str, Any]]
    ) -> str:
        """
        Build a text representation of the Tier B subgraph relevant to this
        batch for inclusion in the anchoring prompt.

        Strategy:
          1. Extract unique entity names from proposed_anchors.
          2. For each name, search the graph (trigram similarity).
          3. Collect unique root nodes (up to _MAX_CONTEXT_ROOTS).
          4. For each root, expand up to _SUBGRAPH_DEPTH hops.
          5. Render nodes + edges as markdown, respecting char budget.
        """
        entity_names = _extract_all_entity_names(signals)
        if not entity_names:
            return "(no relevant subgraph found — this may be an entirely new topic area)"

        root_ids: list[UUID] = []
        seen_ids: set[UUID] = set()

        for name in entity_names:
            if len(root_ids) >= _MAX_CONTEXT_ROOTS:
                break
            nodes = await self._reader.search_nodes(name, limit=2)
            for node in nodes:
                if node.node_id not in seen_ids:
                    seen_ids.add(node.node_id)
                    root_ids.append(node.node_id)

        if not root_ids:
            return "(no matching nodes found in Tier B for the proposed entities)"

        # Expand subgraphs and deduplicate
        all_nodes: dict[UUID, Any] = {}
        all_edges: dict[UUID, Any] = {}

        for root_id in root_ids:
            subgraph = await self._reader.get_subgraph(
                root_id, depth=_SUBGRAPH_DEPTH
            )
            for n in subgraph["nodes"]:
                all_nodes[n.node_id] = n
            for e in subgraph["edges"]:
                all_edges[e.edge_id] = e

        return _render_subgraph_context(
            list(all_nodes.values()),
            list(all_edges.values()),
            char_budget=_SUBGRAPH_CHAR_BUDGET,
        )


# ── Output parsing ────────────────────────────────────────────────────────────


def _parse_anchor_operations(
    content: str,
    *,
    batch_id: str,
) -> tuple[list[ProposedAnchorOperation], str | None]:
    """
    Parse LLM output into a list of ProposedAnchorOperation models.

    Returns (operations, error_message).  On complete failure, operations is
    empty and error_message explains why.  On partial success (some items
    invalid), valid items are returned and error_message summarises skipped
    items.
    """
    raw_list = _extract_json_array(content)
    if raw_list is None:
        return [], f"could not extract JSON array from LLM output (batch={batch_id})"

    if not isinstance(raw_list, list):
        return [], f"LLM output was not a JSON array (batch={batch_id})"

    if not raw_list:
        return [], None

    # Attempt bulk validation first (fast path)
    try:
        ops = _OPERATIONS_ADAPTER.validate_python(raw_list)
        return ops, None
    except ValidationError:
        pass

    # Fallback: validate item-by-item, accumulating valid ones
    valid: list[ProposedAnchorOperation] = []
    skip_count = 0
    adapter: TypeAdapter[ProposedAnchorOperation] = TypeAdapter(ProposedAnchorOperation)

    for item in raw_list:
        try:
            valid.append(adapter.validate_python(item))
        except (ValidationError, Exception):
            skip_count += 1

    error: str | None = None
    if skip_count:
        error = f"skipped {skip_count}/{len(raw_list)} invalid operations (batch={batch_id})"

    return valid, error


def _extract_json_array(text: str) -> list | None:
    """Extract a JSON array from possibly-prose LLM output."""
    text = text.strip()

    # Strip markdown fences
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()

    # Try direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Try extracting the first [...] block
    bracket_match = re.search(r"\[[\s\S]*\]", text)
    if bracket_match:
        try:
            parsed = json.loads(bracket_match.group(0))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    return None


# ── Subgraph rendering ────────────────────────────────────────────────────────


def _extract_all_entity_names(signals: list[dict[str, Any]]) -> list[str]:
    """Collect unique entity names from all signal proposed_anchors."""
    names: list[str] = []
    seen: set[str] = set()
    for sig in signals:
        for anchor in sig.get("proposed_anchors", []):
            if not isinstance(anchor, dict):
                continue
            if anchor.get("operation") != "create_node":
                continue
            name = anchor.get("fields", {}).get("name", "")
            if name and name.lower() not in seen:
                seen.add(name.lower())
                names.append(name)
    return names


def _render_subgraph_context(nodes: list, edges: list, *, char_budget: int) -> str:
    """
    Render a subgraph as a Markdown string for the anchoring prompt.

    Respects `char_budget` by truncating node/edge lists when the budget
    would be exceeded.
    """
    lines: list[str] = []

    lines.append(f"**Tier B subgraph** ({len(nodes)} nodes, {len(edges)} edges)\n")

    lines.append("### Nodes")
    for node in nodes:
        line = (
            f"- [{node.node_type}] **{node.name}** "
            f"(id={node.node_id})"
        )
        if node.description:
            line += f" — {node.description[:120]}"
        lines.append(line)

        current = "\n".join(lines)
        if len(current) > char_budget * 0.6:
            lines.append("  *(node list truncated)*")
            break

    lines.append("\n### Edges")
    for edge in edges:
        line = (
            f"- {edge.source_node_id} "
            f"--{edge.edge_type}--> "
            f"{edge.target_node_id} "
            f"[{edge.current_weight_band}] "
            f"(id={edge.edge_id})"
        )
        lines.append(line)
        if edge.reasoning:
            lines.append(f"  *reasoning*: {edge.reasoning[:200]}")
        if edge.falsification_criteria:
            lines.append(f"  *falsification*: {edge.falsification_criteria[:200]}")

        current = "\n".join(lines)
        if len(current) > char_budget:
            lines.append("  *(edge list truncated)*")
            break

    return "\n".join(lines)
