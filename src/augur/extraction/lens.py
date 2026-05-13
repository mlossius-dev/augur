"""
Lens configuration and base types for the extraction layer.

A lens is a fixed configuration defining what a single LLM pass looks for
in a payload.  Lenses are stateless configuration objects; the LLM call
that uses them is in executor.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from augur.graph.schema import EdgeType, NodeType


@dataclass(frozen=True)
class LensConfig:
    """
    Complete configuration for one extraction lens.

    lens_id           : unique slug (e.g. "commodities")
    lens_version      : semver string; bumped on prompt changes
    system_prompt     : the system prompt sent to the LLM
    graph_scope_nodes : node types this lens is allowed to propose
    graph_scope_edges : edge types this lens is allowed to propose
    model_class       : "cheap" | "mid" | "strong" — maps to model routing
    language_competence: ISO codes; empty = all languages accepted
    max_signals       : per-payload signal cap (guards against over-extraction)
    """

    lens_id: str
    lens_version: str
    system_prompt: str
    graph_scope_nodes: frozenset[NodeType]
    graph_scope_edges: frozenset[EdgeType]
    model_class: str = "cheap"
    language_competence: frozenset[str] = field(default_factory=frozenset)
    max_signals: int = 10


# ── Signal output schema (JSON instructed in the system prompt) ───────────────

SIGNAL_OUTPUT_SCHEMA = """\
Return a JSON array of signal objects.  Each signal object has exactly these fields:

{
  "claim_text": "<concise present-tense statement of the signal; max 200 chars>",
  "confidence_band": "<hard_datum | reported_claim | inference | weak_inference>",
  "reasoning": "<1-2 sentences explaining why you extracted this signal>",
  "proposed_anchors": [
    // zero or more anchor operations; see schema below
  ]
}

Anchor operation schemas:

create_node:
  {"operation":"create_node","node_type":"<type>","proposed_id":"<slug>",
   "fields":{"name":"<canonical name>","<type-specific fields>"},"reasoning":"<why>"}

update_node:
  {"operation":"update_node","target_node_id":"<UUID or proposed_id>",
   "field_updates":{"<field>":"<value>"},"reasoning":"<why>"}

create_edge:
  {"operation":"create_edge","source_node_id":"<UUID or proposed_id>",
   "target_node_id":"<UUID or proposed_id>","edge_type":"<type>",
   "proposed_weight_band":"<strong|moderate|weak|provisional|disputed>",
   "reasoning":"<why>","falsification_criteria":"<what would weaken this edge>"}

update_edge_weight:
  {"operation":"update_edge_weight","target_edge_id":"<UUID>",
   "new_weight_band":"<band>","direction":"<strengthen|weaken>","reasoning":"<why>"}

add_supporting_signal / add_disconfirming_signal:
  {"operation":"add_supporting_signal","target_edge_id":"<UUID>","signal_id":"<UUID>"}

Rules:
- If the payload contains nothing relevant to your lens scope, return an empty array [].
- Do NOT invent content not present in the payload.
- Prefer updating existing nodes/edges (use their UUIDs from the context) over creating duplicates.
- Every create_edge MUST include a non-empty falsification_criteria.
- Weight bands for new edges should be provisional or weak unless the signal is very clear.
- Maximum {max_signals} signals per payload.
"""
