"""
Model routing configuration.

Maps pipeline stages to OpenRouter model identifiers.  All model selection
is runtime configuration; changing the model for a stage requires no code
changes, only config updates.

The architecture document's guidance:
  - Lens extraction  → small/fast (high volume, cost-sensitive)
  - Anchoring        → mid-strength (structured graph reasoning)
  - Disconfirmation  → strong (quality matters; runs infrequently)
  - Projection       → mid to strong
  - Conversation     → free-tier only (separate API key)
"""

from __future__ import annotations

from enum import StrEnum


class PipelineStage(StrEnum):
    """Named pipeline stages that drive model selection."""

    EXTRACTION = "extraction"
    ANCHORING = "anchoring"
    DISCONFIRMATION = "disconfirmation"
    PROJECTION = "projection"
    CONVERSATION = "conversation"


# Default model routing table.  Override individual entries via environment
# variables named AUGUR_MODEL_<STAGE> (e.g. AUGUR_MODEL_EXTRACTION).
DEFAULT_MODEL_ROUTING: dict[PipelineStage, str] = {
    # Small/fast models for high-volume extraction
    PipelineStage.EXTRACTION: "google/gemini-flash-1.5",
    # Mid-strength for anchoring — needs structured reasoning over graph state
    PipelineStage.ANCHORING: "anthropic/claude-3.5-haiku",
    # Strong model for disconfirmation — quality over cost
    PipelineStage.DISCONFIRMATION: "anthropic/claude-opus-4",
    # Mid to strong for projection
    PipelineStage.PROJECTION: "anthropic/claude-3.5-sonnet",
    # Free-tier for conversation (routes through the free-tier API key)
    PipelineStage.CONVERSATION: "google/gemini-2.0-flash-exp:free",
}

# Stages that must use the free-tier OpenRouter key.
# The LLM client enforces this split; calling code never chooses the key.
FREE_TIER_STAGES: frozenset[PipelineStage] = frozenset({PipelineStage.CONVERSATION})
