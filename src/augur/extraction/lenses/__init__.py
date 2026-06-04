"""
Augur extraction lens catalog.

ACTIVE_LENSES: the standard lenses run in parallel for every payload.
DISCONFIRMATION_LENS: the inline disconfirmation lens; handled separately by
  the executor because it requires graph context (high-weight edge list).
"""

from augur.extraction.lenses.commodities import COMMODITIES_LENS
from augur.extraction.lenses.disconfirmation import (
    DISCONFIRMATION_LENS,
    build_disconfirmation_system_prompt,
)
from augur.extraction.lenses.financial import FINANCIAL_LENS
from augur.extraction.lenses.geopolitical import GEOPOLITICAL_LENS
from augur.extraction.lenses.narrative_divergence import NARRATIVE_DIVERGENCE_LENS
from augur.extraction.lenses.physical_world import PHYSICAL_WORLD_LENS
from augur.extraction.lenses.regulatory import REGULATORY_LENS

# Standard lenses run in parallel for every payload.
# Order does not matter — they run concurrently.
ACTIVE_LENSES = [
    COMMODITIES_LENS,
    FINANCIAL_LENS,
    GEOPOLITICAL_LENS,
    PHYSICAL_WORLD_LENS,
    REGULATORY_LENS,
    NARRATIVE_DIVERGENCE_LENS,
]

__all__ = [
    "ACTIVE_LENSES",
    "COMMODITIES_LENS",
    "FINANCIAL_LENS",
    "GEOPOLITICAL_LENS",
    "PHYSICAL_WORLD_LENS",
    "REGULATORY_LENS",
    "NARRATIVE_DIVERGENCE_LENS",
    "DISCONFIRMATION_LENS",
    "build_disconfirmation_system_prompt",
]
