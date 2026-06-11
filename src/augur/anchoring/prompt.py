"""
Anchoring prompt templates.

The anchoring prompt has five sections as specified in augur-signal-pipeline.md:
  1. Role and goal
  2. Schema definition (compact inline reference)
  3. Current subgraph (relevant neighborhood from Tier B)
  4. Signal batch (claims + proposed_anchors from Tier A)
  5. Output instruction

The rendered prompt is sent to a mid/strong model via the LLM client.
"""

from __future__ import annotations

from typing import Any

# ── Section 1: Role (included in system prompt) ───────────────────────────────

SYSTEM_ROLE = """\
You are the **anchoring stage** of Augur, an intelligence reasoning system.

Your job is to convert a batch of signal clusters from Tier A (working memory)
into a structured list of graph update operations for Tier B (the durable
causal graph).

You work at the boundary between noisy signal and durable knowledge.  Your
decisions are the primary mechanism through which new information enters the
graph, so they must be conservative, well-reasoned, and anchored to the
evidence actually present in the signal batch.
"""

# ── Section 2: Schema reference ────────────────────────────────────────────────

SCHEMA_REFERENCE = """\
## Graph schema (compact reference)

### Node types
- **entity**: A named actor, object, or system.
  Required fields: name (str), entity_kind (state|organization|company|place|
  infrastructure|sector|commodity|currency|instrument)
- **condition**: An ongoing state of the world.
  Required fields: name (str), current_state (active|inactive|partially_active|
  disputed|unknown)
- **event**: A discrete occurrence at a point in time.
  Required fields: name (str), event_kind (geopolitical|economic|physical|
  policy|corporate|natural), occurred_at (ISO 8601 datetime)
- **quantity**: A measurable value with a unit.
  Required fields: name (str), unit (str)
- **scenario**: A projected future state (operator-created; avoid proposing these).
- **claim**: An interpretive statement about the world.
  Required fields: name (str), claim_text (str), claim_kind (factual|
  interpretive|contested)

### Edge types
causes | enables | constrains | accelerates | correlates_with | contradicts |
refines | part_of | produces

All edges require:
- **reasoning**: why this causal/relational link exists
- **falsification_criteria**: what evidence would weaken this edge (REQUIRED;
  empty string causes applier rejection)
- **proposed_weight_band**: strong|moderate|weak|provisional|disputed

Weight discipline: new edges from a single signal cluster should be
**provisional** or **weak** unless the evidence is unusually clear.

### Anchor operations
```json
{"operation": "create_node", "node_type": "<type>", "proposed_id": "<slug>",
 "fields": {"name": "...", <type-specific fields>}, "reasoning": "..."}

{"operation": "update_node", "target_node_id": "<UUID or proposed_id>",
 "field_updates": {"<field>": "<value>"}, "reasoning": "..."}

{"operation": "create_edge", "source_node_id": "<UUID or proposed_id>",
 "target_node_id": "<UUID or proposed_id>", "edge_type": "<type>",
 "proposed_weight_band": "<band>", "reasoning": "...",
 "falsification_criteria": "..."}

{"operation": "update_edge_weight", "target_edge_id": "<UUID>",
 "new_weight_band": "<band>", "direction": "strengthen|weaken",
 "reasoning": "..."}

{"operation": "add_supporting_signal", "target_edge_id": "<UUID>",
 "signal_id": "<UUID>"}

{"operation": "add_disconfirming_signal", "target_edge_id": "<UUID>",
 "signal_id": "<UUID>"}
```
"""

# ── Section 5: Output instruction ─────────────────────────────────────────────

OUTPUT_INSTRUCTION = """\
## Your task

Produce a JSON array of anchor operations that best captures the signal batch
given the current graph state.

**Anchoring discipline:**
1. **Reuse existing nodes.** If a signal refers to an entity already in the
   subgraph (same UUID or alias), use its UUID — do not create a duplicate.
2. **Reuse existing edges.** Strengthening or weakening an existing edge is
   preferred over creating a parallel one.
3. **Conservative weight bands.** Start new edges at provisional or weak.
   Only use moderate if multiple corroborating signals agree. Strong requires
   very clear, direct evidence.
4. **Every create_edge must have a non-empty falsification_criteria.**
5. **proposed_id slugs** must be alphanumeric + underscore + hyphen only.
6. If nothing in the signal batch warrants a graph mutation, return [].
7. Do NOT invent nodes or edges not supported by the signal batch.

**Bootstrapping a sparse or empty graph.** When the subgraph context above is
empty or contains no nodes relevant to these signals, there is simply nothing
to reuse yet — this is expected early in the graph's life and is NOT a reason to
return an empty result. Create the entity, condition, and event nodes the
signal batch clearly supports, plus the well-evidenced edges between them (still
at conservative weight bands). Rule 1 ("reuse existing nodes") applies only when
matching nodes actually exist.

Return ONLY a valid JSON array. No prose, no markdown fences.
"""


def build_user_message(
    *,
    subgraph_context: str,
    signal_batch: list[dict[str, Any]],
) -> str:
    """
    Assemble the user-turn message for the anchoring LLM call.

    Combines sections 3 (subgraph) and 4 (signals) into a single user message.
    The system turn already contains sections 1 and 2.
    """
    signals_text = _format_signals(signal_batch)

    return (
        f"## Current subgraph context\n\n"
        f"{subgraph_context}\n\n"
        f"---\n\n"
        f"## Signal batch ({len(signal_batch)} signal(s))\n\n"
        f"{signals_text}\n\n"
        f"---\n\n"
        f"{OUTPUT_INSTRUCTION}"
    )


def build_system_prompt() -> str:
    """Combine role + schema into the system prompt."""
    return f"{SYSTEM_ROLE}\n\n{SCHEMA_REFERENCE}"


def _format_signals(signals: list[dict[str, Any]]) -> str:
    """Render a signal batch as readable text for the LLM."""
    lines: list[str] = []
    for i, sig in enumerate(signals, 1):
        lines.append(f"### Signal {i} (signal_id: {sig.get('signal_id', 'N/A')})")
        lines.append(f"- **lens**: {sig.get('lens_id', '?')}")
        lines.append(f"- **confidence_band**: {sig.get('confidence_band', '?')}")
        lines.append(f"- **content_timestamp**: {sig.get('content_timestamp', '?')}")
        lines.append(f"- **claim**: {sig.get('claim_text', '')}")
        if sig.get("reasoning"):
            lines.append(f"- **lens reasoning**: {sig['reasoning']}")

        anchors = sig.get("proposed_anchors", [])
        if anchors:
            lines.append(f"- **proposed_anchors** ({len(anchors)} operations):")
            for anchor in anchors:
                op = anchor.get("operation", "?")
                brief = _anchor_brief(anchor)
                lines.append(f"  - [{op}] {brief}")
        lines.append("")
    return "\n".join(lines)


def _anchor_brief(anchor: dict[str, Any]) -> str:
    """One-line summary of an anchor operation for the LLM context."""
    op = anchor.get("operation", "")
    if op == "create_node":
        fields = anchor.get("fields", {})
        return f"create {anchor.get('node_type')} '{fields.get('name', '?')}' (proposed_id={anchor.get('proposed_id')})"
    elif op == "update_node":
        return f"update node {anchor.get('target_node_id')} fields={list(anchor.get('field_updates', {}).keys())}"
    elif op == "create_edge":
        return (
            f"{anchor.get('source_node_id')} --{anchor.get('edge_type')}--> "
            f"{anchor.get('target_node_id')} [{anchor.get('proposed_weight_band')}]"
        )
    elif op == "update_edge_weight":
        return f"{anchor.get('direction')} edge {anchor.get('target_edge_id')} → {anchor.get('new_weight_band')}"
    elif op in ("add_supporting_signal", "add_disconfirming_signal"):
        return f"signal {anchor.get('signal_id')} on edge {anchor.get('target_edge_id')}"
    return str(anchor)[:80]
