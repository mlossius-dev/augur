"""
Model routing configuration.

Maps pipeline stages to OpenRouter model identifiers.  All model selection
is runtime configuration: override a stage with AUGUR_MODEL_<STAGE>
(e.g. AUGUR_MODEL_EXTRACTION=<vendor>/<model>:free).

Every stage defaults to OpenRouter's free models router (`openrouter/free`),
which picks an available free model per request.  This is an operator
decision (October 2026): the paid budget was too high for a system that is
not a product, and lower extraction quality is an accepted risk.  The free
tier is rate limited per account (see Settings.llm_daily_call_limit), so
volume, not cost, is now the constraint.

The architecture document's strength guidance still describes what each
stage would ideally use if a paid model is configured:
  - Lens extraction  → small/fast (high volume)
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
    DIGEST = "digest"


FREE_MODELS_ROUTER = "openrouter/free"

# Default model routing table.  Override per stage with AUGUR_MODEL_<STAGE>.
DEFAULT_MODEL_ROUTING: dict[PipelineStage, str] = {
    stage: FREE_MODELS_ROUTER for stage in PipelineStage
}


def is_free_model(model: str) -> bool:
    """True for OpenRouter's free router and for any `:free` model variant."""
    return model == FREE_MODELS_ROUTER or model.endswith(":free")


def routing_from_overrides(overrides: dict[PipelineStage, str]) -> dict[PipelineStage, str]:
    """The default routing with each non-empty override applied."""
    routing = dict(DEFAULT_MODEL_ROUTING)
    routing.update({stage: model for stage, model in overrides.items() if model})
    return routing


# Stages that must use the free-tier OpenRouter key.
# The LLM client enforces this split; calling code never chooses the key.
FREE_TIER_STAGES: frozenset[PipelineStage] = frozenset(
    {PipelineStage.CONVERSATION, PipelineStage.DIGEST}
)
