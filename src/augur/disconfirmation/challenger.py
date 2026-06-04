"""
Challenge prompt builder for the periodic disconfirmation pass.

Constructs a targeted LLM prompt for each edge under review, using
the strong model (PipelineStage.DISCONFIRMATION → claude-opus-4).

The challenge prompt gives the model:
  1. The edge details: source/target nodes, weight, reasoning, falsification criteria.
  2. A compact window of recent signals from Tier A that touch the edge's
     neighbourhood.
  3. A direct question: does any evidence in this window meet the
     falsification criteria?

Output shapes:
  - Disconfirmation found:
      {"outcome": "found",
       "reasoning": "...",
       "operations": [
         {"operation": "update_edge_weight", ...},   // optional
         {"operation": "add_disconfirming_signal", ...}  // one or more
       ]}
  - No disconfirmation found:
      {"outcome": "not_found",
       "reasoning": "..."}
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_CHALLENGE_SYSTEM_PROMPT = """\
You are the **disconfirmation pass** component of the Augur intelligence system.

Your job is to be the graph's sceptic. You are presented with one edge from
the causal graph and a window of recent signals. Your task is to determine
whether any evidence in the recent window meets the edge's falsification
criteria — that is, whether the evidence would, if credible, justify weakening
or removing the edge.

You are NOT looking for corroboration. You are looking for the bear case.

## Output format

You must return a single JSON object (not an array):

If disconfirmation evidence found:
{
  "outcome": "found",
  "reasoning": "<2-4 sentences explaining what evidence meets the falsification criteria and why>",
  "operations": [
    // at least one of:
    {"operation": "add_disconfirming_signal", "target_edge_id": "<UUID>", "signal_id": "<signal UUID>"},
    // optionally, if the evidence is strong enough to weaken the edge:
    {"operation": "update_edge_weight", "target_edge_id": "<UUID>",
     "new_weight_band": "<weaker band>", "direction": "weaken",
     "reasoning": "<why this evidence justifies weakening>"}
  ]
}

If no disconfirmation evidence found:
{
  "outcome": "not_found",
  "reasoning": "<2-4 sentences explaining why the recent evidence does NOT meet the falsification criteria, and what would be needed>"
}

Rules:
- Be conservative. If the evidence is ambiguous, prefer not_found.
- Only weaken an edge if the evidence clearly and directly meets the
  falsification criteria. Do not weaken based on tangential signals.
- If proposing update_edge_weight, the new_weight_band must be one step weaker
  than the current band.
- Return ONLY the JSON object. No prose, no markdown fences.
"""

_WEIGHT_BAND_ORDER = ["strong", "moderate", "weak", "provisional", "disputed"]


def build_challenge_prompt(
    edge: dict[str, Any],
    recent_signals: list[dict[str, Any]],
) -> str:
    """
    Build the user-turn message for a single edge challenge.

    Returns the formatted user message; the system prompt is separate.
    """
    edge_section = _format_edge(edge)
    signals_section = _format_signals(recent_signals)

    return (
        f"{edge_section}\n\n"
        f"---\n\n"
        f"{signals_section}\n\n"
        f"---\n\n"
        f"## Your task\n\n"
        f"Given the edge's falsification criteria above, does any evidence "
        f"in the recent signal window meet those criteria?\n\n"
        f"If yes: identify which signals meet the criteria and propose "
        f"the appropriate disconfirmation operations.\n\n"
        f"If no: explain concisely why the recent evidence does not weaken "
        f"this edge.\n\n"
        f"Return ONLY a valid JSON object."
    )


def parse_challenge_output(
    content: str,
    *,
    edge_id: str,
) -> dict[str, Any]:
    """
    Parse the LLM's challenge response.

    Returns a dict with at minimum:
      {"outcome": "found" | "not_found" | "error",
       "reasoning": "...",
       "operations": [...]}   # empty for not_found

    Never raises; errors produce outcome="error".
    """
    text = content.strip()

    # Strip markdown fences if present
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to find a {...} block
        brace_match = re.search(r"\{[\s\S]*\}", text)
        if brace_match:
            try:
                parsed = json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                log.warning("challenger.parse_failed", edge_id=edge_id, preview=text[:200])
                return {"outcome": "error", "reasoning": "Could not parse LLM output.", "operations": []}
        else:
            log.warning("challenger.parse_failed", edge_id=edge_id, preview=text[:200])
            return {"outcome": "error", "reasoning": "No JSON found in LLM output.", "operations": []}

    if not isinstance(parsed, dict):
        return {"outcome": "error", "reasoning": "LLM returned non-object JSON.", "operations": []}

    outcome = parsed.get("outcome", "error")
    if outcome not in ("found", "not_found", "error"):
        outcome = "error"

    return {
        "outcome": outcome,
        "reasoning": str(parsed.get("reasoning", ""))[:2000],
        "operations": parsed.get("operations", []) if outcome == "found" else [],
    }


def one_step_weaker(weight_band: str) -> str | None:
    """
    Return the next weaker weight band, or None if already at minimum.

    Used to validate that update_edge_weight proposals don't skip bands.
    """
    try:
        idx = _WEIGHT_BAND_ORDER.index(weight_band)
        if idx < len(_WEIGHT_BAND_ORDER) - 1:
            return _WEIGHT_BAND_ORDER[idx + 1]
    except ValueError:
        pass
    return None


# ── Formatters ────────────────────────────────────────────────────────────────


def _format_edge(edge: dict[str, Any]) -> str:
    supporting = edge.get("supporting_signals") or []
    disconfirming = edge.get("disconfirming_signals") or []
    last_challenged = edge.get("last_disconfirmation_pass")

    return (
        f"## Edge under review\n\n"
        f"- **Edge ID**: `{edge['edge_id']}`\n"
        f"- **Source node**: {edge.get('source_name', '?')} (`{edge['source_node_id']}`)\n"
        f"- **Target node**: {edge.get('target_name', '?')} (`{edge['target_node_id']}`)\n"
        f"- **Edge type**: {edge.get('edge_type', '?')}\n"
        f"- **Current weight**: {edge.get('current_weight_band', '?')}\n"
        f"- **Supporting signals**: {len(supporting)}\n"
        f"- **Disconfirming signals**: {len(disconfirming)}\n"
        f"- **Last challenged**: {last_challenged or 'never'}\n"
        f"- **Created at**: {edge.get('created_at', '?')}\n\n"
        f"### Reasoning (why this edge exists)\n\n"
        f"{edge.get('reasoning', '(none)')}\n\n"
        f"### Falsification criteria\n\n"
        f"{edge.get('falsification_criteria', '(none)')}"
    )


def _format_signals(signals: list[dict[str, Any]]) -> str:
    if not signals:
        return "## Recent signal window\n\n*(no recent signals found for this edge neighbourhood)*"

    lines = [f"## Recent signal window ({len(signals)} signal(s))\n"]
    for i, sig in enumerate(signals, 1):
        lines.append(f"### Signal {i} (`{sig.get('signal_id', 'N/A')}`)")
        lines.append(f"- **lens**: {sig.get('lens_id', '?')}")
        lines.append(f"- **confidence**: {sig.get('confidence_band', '?')}")
        lines.append(f"- **timestamp**: {sig.get('content_timestamp', '?')}")
        lines.append(f"- **claim**: {sig.get('claim_text', '')}")
        if sig.get("reasoning"):
            lines.append(f"- **reasoning**: {sig['reasoning'][:300]}")
        lines.append("")
    return "\n".join(lines)
