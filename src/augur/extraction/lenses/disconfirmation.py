"""
Disconfirmation lens — Phase 4 (inline variant).

A bear-case lens that reads each payload specifically for evidence that
contradicts existing high-weight edges in the graph.  Unlike other lenses
this lens CANNOT create new nodes or new edges — it only produces
add_disconfirming_signal and update_edge_weight operations on existing edges.

Two modes:
  1. Inline (this module): runs during normal extraction.  Receives a
     compact list of high-weight edges from the caller.
  2. Periodic pass (Phase 5): a separate orchestrator that selects specific
     edges for systematic challenge.

The inline variant is lightweight: it receives the N highest-weight edges
whose falsification_criteria are most plausibly testable by recent content,
and asks "does this payload provide evidence meeting those criteria?"
"""

from __future__ import annotations

from augur.extraction.lens import LensConfig
from augur.graph.schema import EdgeType, NodeType

# The disconfirmation lens uses a specialised output schema — it can only
# produce add_disconfirming_signal and update_edge_weight operations.
_DISCONFIRMATION_OUTPUT_SCHEMA = """\
Return a JSON array of signal objects.  Each signal may produce ONLY the
following anchor operations:

  add_disconfirming_signal:
    {"operation":"add_disconfirming_signal",
     "target_edge_id":"<UUID of the edge being challenged>",
     "signal_id":"<leave blank; will be filled by the system>"}

  update_edge_weight (weaken only):
    {"operation":"update_edge_weight",
     "target_edge_id":"<UUID>",
     "new_weight_band":"<weaker band>",
     "direction":"weaken",
     "reasoning":"<why this payload weakens the edge>"}

Signal structure:
{
  "claim_text": "<what in the payload challenges the edge>",
  "confidence_band": "<reported_claim | inference | weak_inference>",
  "reasoning": "<why this constitutes disconfirmation; cite falsification_criteria>",
  "proposed_anchors": [... only add_disconfirming_signal or update_edge_weight ...]
}

If the payload contains no evidence meeting any edge's falsification criteria,
return [].

Do NOT create new nodes or new edges.
Do NOT produce add_supporting_signal operations.
Maximum 5 signals per payload.
"""


def build_disconfirmation_system_prompt(edge_context: str) -> str:
    """
    Build the system prompt for an inline disconfirmation extraction call.

    Args:
        edge_context: Formatted text listing the high-weight edges to challenge,
                      including their UUIDs, types, and falsification criteria.
    """
    return f"""\
You are the **disconfirmation lens** of the Augur intelligence system.

Your sole job is to read the provided payload and identify evidence that
weakens or contradicts existing high-weight causal edges in the graph.

You are the system's self-scepticism mechanism. You are not looking for
corroboration — other lenses handle that. You are looking for the
**bear case**: evidence that, if credible, would weaken our current
understanding of how things relate to each other.

## High-weight edges under review

The following edges from Tier B have high weight and non-trivial
falsification criteria.  For each, ask: *"Does this payload provide
evidence meeting the falsification criteria?"*

{edge_context}

## What you look for

- Any factual claim in the payload that directly addresses one of the
  falsification criteria listed above.
- Data, statements, or observations that contradict the causal direction
  implied by an edge (e.g. "A causes B" but the payload shows A happening
  without B, or B weakening despite A).
- Authoritative reversals: a government, institution, or verified source
  walking back a previously stated position that supported an edge.

## What you ignore

- Speculative commentary without factual backing.
- Evidence that only weakly relates to the criteria (if in doubt, use
  weak_inference and still include it).
- Any signal that supports or corroborates an edge (that is not your job).

## Output constraints

- You CANNOT create new nodes.
- You CANNOT create new edges.
- You CANNOT produce add_supporting_signal operations.
- You MAY only produce add_disconfirming_signal and update_edge_weight
  (direction: weaken) operations.

""" + _DISCONFIRMATION_OUTPUT_SCHEMA


DISCONFIRMATION_LENS = LensConfig(
    lens_id="disconfirmation",
    lens_version="1",
    # The system prompt template is built dynamically (needs edge context).
    # This static version is used only for configuration inspection.
    system_prompt=(
        "Disconfirmation lens — system prompt built dynamically per call. "
        "See build_disconfirmation_system_prompt()."
    ),
    # Cannot create any new nodes — empty set
    graph_scope_nodes=frozenset(),
    # Can only reference existing edges via add_disconfirming_signal /
    # update_edge_weight.  No create_edge allowed.
    graph_scope_edges=frozenset(),
    model_class="cheap",
    max_signals=5,
)
