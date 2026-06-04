"""
LLM prompt templates for scenario generation.

The prompt asks the model to generate 3-5 scenarios for the given dimension
from structured graph evidence.  Output is a JSON array.
"""

from __future__ import annotations

from augur.projection.models import GraphEvidence

SYSTEM_PROMPT = """\
You are a geopolitical and macroeconomic analyst with expertise in scenario \
planning.  You will be given structured evidence from a causal graph \
representing the current state of the world.

Your task is to generate 3-5 plausible near-term scenarios based solely on \
the evidence provided.  Do not use information from your training data beyond \
what is present in the graph evidence.

Each scenario must be grounded in at least one of the active conditions or \
strong causal links listed in the evidence.

Output ONLY a JSON array.  Each element must have exactly these fields:
  - title: string (≤12 words, no filler phrases)
  - summary: string (2-4 sentences; what happens, why, and what it means)
  - probability_band: one of "high" | "moderate" | "low" | "negligible"
  - time_horizon: string (e.g. "1-3 months", "3-6 months", "6-12 months")
  - supporting_evidence: list of short strings (≤3) citing graph conditions or links
  - contradicting_evidence: list of short strings (≤2) or empty list

Output nothing except the JSON array.  Do not wrap it in markdown code fences.\
"""


def build_user_message(evidence: GraphEvidence, *, dimension_label: str | None) -> str:
    dim_header = f"Dimension: {dimension_label}" if dimension_label else "Dimension: Cross-cutting / global"

    conditions_block = "\n".join(
        f"  • [{c['node_id'][:8]}] {c['name']}"
        + (f": {c['description'][:120]}" if c["description"] else "")
        for c in evidence.active_conditions
    ) or "  (none)"

    edges_block = "\n".join(
        f"  • [{e['edge_id'][:8]}] {e['source_name']} --{e['edge_type'].replace('_',' ')}--> "
        f"{e['target_name']} [{e['weight_band']}]"
        for e in evidence.strong_edges
    ) or "  (none)"

    changes_block = "\n".join(
        f"  • {c['summary']} ({c['occurred_at'][:10]})"
        for c in evidence.recent_changes
    ) or "  (none in the last 14 days)"

    return f"""{dim_header}

=== Active conditions (currently flagged as active) ===
{conditions_block}

=== Strong causal links (strong or moderate weight) ===
{edges_block}

=== Recent graph changes (last 14 days) ===
{changes_block}

Generate 3-5 plausible near-term scenarios based on the above evidence."""
