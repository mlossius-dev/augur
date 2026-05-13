"""
Augur graph schema: enumerations, constants, and vocabulary.

All names here mirror the database enums in migration 001_graph_schema.sql
and the vocabulary in docs/augur-graph-schema.md.  Code throughout the
project imports from this module rather than using string literals.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum


class NodeType(StrEnum):
    ENTITY = "entity"
    CONDITION = "condition"
    EVENT = "event"
    QUANTITY = "quantity"
    SCENARIO = "scenario"
    CLAIM = "claim"


class EdgeType(StrEnum):
    # Causal
    CAUSES = "causes"
    ENABLES = "enables"
    CONSTRAINS = "constrains"
    ACCELERATES = "accelerates"
    # Relational
    CORRELATES_WITH = "correlates_with"
    CONTRADICTS = "contradicts"
    REFINES = "refines"
    # Structural
    PART_OF = "part_of"
    PRODUCES = "produces"


class WeightBand(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    PROVISIONAL = "provisional"
    DISPUTED = "disputed"


class ConfidenceBand(StrEnum):
    HARD_DATUM = "hard_datum"
    REPORTED_CLAIM = "reported_claim"
    INFERENCE = "inference"
    WEAK_INFERENCE = "weak_inference"


class ConditionState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    PARTIALLY_ACTIVE = "partially_active"
    DISPUTED = "disputed"
    UNKNOWN = "unknown"


class EntityKind(StrEnum):
    STATE = "state"
    ORGANIZATION = "organization"
    COMPANY = "company"
    PLACE = "place"
    INFRASTRUCTURE = "infrastructure"
    SECTOR = "sector"
    COMMODITY = "commodity"
    CURRENCY = "currency"
    INSTRUMENT = "instrument"


class EventKind(StrEnum):
    GEOPOLITICAL = "geopolitical"
    ECONOMIC = "economic"
    PHYSICAL = "physical"
    POLICY = "policy"
    CORPORATE = "corporate"
    NATURAL = "natural"


class ClaimKind(StrEnum):
    FACTUAL = "factual"
    INTERPRETIVE = "interpretive"
    CONTESTED = "contested"


class ClaimAssessment(StrEnum):
    WELL_SUPPORTED = "well_supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONTESTED = "contested"
    WEAKLY_SUPPORTED = "weakly_supported"
    NOT_SUPPORTED = "not_supported"


# Numeric anchors for the five ordinal weight bands.
# Used ONLY for projection-time arithmetic (chaining weights along a graph path).
# Never reported to users as probabilities.
WEIGHT_BAND_ANCHORS: dict[WeightBand, Decimal] = {
    WeightBand.STRONG: Decimal("0.8"),
    WeightBand.MODERATE: Decimal("0.6"),
    WeightBand.WEAK: Decimal("0.4"),
    WeightBand.PROVISIONAL: Decimal("0.2"),
    # disputed has no anchor — edges in this state are flagged, not multiplied
    WeightBand.DISPUTED: Decimal("0"),
}

# Maximum proposed_anchors a single signal may emit.
# More than this suggests the lens is over-extracting.
MAX_ANCHORS_PER_SIGNAL: int = 10

# AGE graph name (must match migration 001)
AGE_GRAPH_NAME = "augur_graph"

# Capitalised label names as used in AGE vertex/edge creation
AGE_NODE_LABELS: dict[NodeType, str] = {
    NodeType.ENTITY: "Entity",
    NodeType.CONDITION: "Condition",
    NodeType.EVENT: "Event",
    NodeType.QUANTITY: "Quantity",
    NodeType.SCENARIO: "Scenario",
    NodeType.CLAIM: "Claim",
}
