"""
Pydantic models for the Augur graph layer.

Three groups:

1. Node type data models — the type-specific fields stored in nodes.type_data.
2. Edge model — full edge representation including history reference.
3. Proposed anchor operations — the structured output produced by extraction
   LLMs and consumed by the Applier.  These are the inputs to the Applier, not
   the graph state itself.
4. GraphUpdateEvent — the immutable record written to graph_update_events after
   an anchor is applied or rejected.

All models use strict Pydantic v2 semantics.  Models that cross layer
boundaries (returned from the DB, sent to LLMs, persisted as JSONB) use
model_config = ConfigDict(frozen=True) to prevent accidental mutation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Union
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from augur.graph.schema import (
    ClaimAssessment,
    ClaimKind,
    ConditionState,
    ConfidenceBand,
    EdgeType,
    EntityKind,
    EventKind,
    NodeType,
    WeightBand,
)

# ── Node type-data models ─────────────────────────────────────────────────────
# These are stored as JSONB in nodes.type_data and validated at Applier time.


class EntityData(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_kind: EntityKind
    aliases: list[str] = Field(default_factory=list)


class ConditionData(BaseModel):
    model_config = ConfigDict(frozen=True)

    current_state: ConditionState = ConditionState.UNKNOWN
    current_state_confidence: WeightBand | None = None
    subject_entities: list[UUID] = Field(default_factory=list)


class EventData(BaseModel):
    model_config = ConfigDict(frozen=True)

    occurred_at: datetime
    event_kind: EventKind
    # GeoJSON-compatible or free-text geographic reference; PostGIS in a later phase
    occurred_location: str | None = None
    subject_entities: list[UUID] = Field(default_factory=list)


class QuantityData(BaseModel):
    model_config = ConfigDict(frozen=True)

    unit: str
    time_series_reference: dict[str, Any] | None = None
    current_value: float | None = None
    current_value_as_of: datetime | None = None
    subject_entities: list[UUID] = Field(default_factory=list)


class ScenarioData(BaseModel):
    model_config = ConfigDict(frozen=True)

    precondition_nodes: list[UUID] = Field(default_factory=list)
    projected_trajectory: str
    # "user:<id>" or "projection_engine"
    created_by: str = "projection_engine"


class ClaimData(BaseModel):
    model_config = ConfigDict(frozen=True)

    claim_text: str
    claim_kind: ClaimKind
    evidence_for: list[UUID] = Field(default_factory=list)
    evidence_against: list[UUID] = Field(default_factory=list)
    current_assessment: ClaimAssessment = ClaimAssessment.WEAKLY_SUPPORTED
    subject_entities: list[UUID] = Field(default_factory=list)


# Union for use in generic code that handles all node types
NodeTypeData = Union[EntityData, ConditionData, EventData, QuantityData, ScenarioData, ClaimData]


# ── Graph node (as read from DB) ──────────────────────────────────────────────


class Node(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: UUID
    node_type: NodeType
    name: str
    description: str | None
    type_data: dict[str, Any]
    created_from: list[UUID] = Field(default_factory=list)
    langfuse_trace_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ── Graph edge (as read from DB) ──────────────────────────────────────────────


class Edge(BaseModel):
    model_config = ConfigDict(frozen=True)

    edge_id: UUID
    source_node_id: UUID
    target_node_id: UUID
    edge_type: EdgeType
    current_weight_band: WeightBand
    supporting_signals: list[UUID] = Field(default_factory=list)
    disconfirming_signals: list[UUID] = Field(default_factory=list)
    reasoning: str
    falsification_criteria: str
    last_disconfirmation_pass: datetime | None = None
    created_from: list[UUID] = Field(default_factory=list)
    langfuse_trace_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    deprecated: bool = False
    deprecated_at: datetime | None = None


# ── Proposed anchor operations ────────────────────────────────────────────────
# These are the structured outputs the extraction/anchoring LLM produces.
# The Applier validates and applies them.


class CreateNodeOperation(BaseModel):
    """Propose creating a new graph node."""

    operation: Literal["create_node"] = "create_node"
    node_type: NodeType
    # Slug used for forward-reference within the same batch (e.g. "iran_israel_war_2026")
    proposed_id: str
    fields: dict[str, Any]
    reasoning: str

    @field_validator("proposed_id")
    @classmethod
    def proposed_id_is_slug(cls, v: str) -> str:
        if not v or not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                f"proposed_id must be a slug (alphanumeric + _ + -), got: {v!r}"
            )
        return v.lower()


class UpdateNodeOperation(BaseModel):
    """Propose updating fields on an existing node."""

    operation: Literal["update_node"] = "update_node"
    target_node_id: str  # UUID string or proposed_id from same batch
    field_updates: dict[str, Any]
    reasoning: str


class CreateEdgeOperation(BaseModel):
    """Propose creating a new graph edge."""

    operation: Literal["create_edge"] = "create_edge"
    source_node_id: str  # UUID string or proposed_id from same batch
    target_node_id: str
    edge_type: EdgeType
    proposed_weight_band: WeightBand
    reasoning: str
    falsification_criteria: str  # required; Applier rejects if empty

    @field_validator("falsification_criteria")
    @classmethod
    def falsification_criteria_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("falsification_criteria must not be empty")
        return v


class UpdateEdgeWeightOperation(BaseModel):
    """Propose changing an edge's weight band."""

    operation: Literal["update_edge_weight"] = "update_edge_weight"
    target_edge_id: str  # UUID string
    new_weight_band: WeightBand
    direction: Literal["strengthen", "weaken"]
    reasoning: str


class AddSupportingSignalOperation(BaseModel):
    """Record that a signal corroborates an existing edge."""

    operation: Literal["add_supporting_signal"] = "add_supporting_signal"
    target_edge_id: str
    signal_id: str


class AddDisconfirmingSignalOperation(BaseModel):
    """Record that a signal challenges an existing edge."""

    operation: Literal["add_disconfirming_signal"] = "add_disconfirming_signal"
    target_edge_id: str
    signal_id: str


# Discriminated union of all anchor operations
ProposedAnchorOperation = Annotated[
    Union[
        CreateNodeOperation,
        UpdateNodeOperation,
        CreateEdgeOperation,
        UpdateEdgeWeightOperation,
        AddSupportingSignalOperation,
        AddDisconfirmingSignalOperation,
    ],
    Field(discriminator="operation"),
]


# ── GraphUpdateEvent (immutable mutation record) ──────────────────────────────


class GraphUpdateEvent(BaseModel):
    """
    An immutable record of one graph mutation, written to graph_update_events.

    Both applied and rejected operations produce an event.  Rejected events
    have rejected=True and a rejection_reason; they are never applied to the
    graph but remain permanently auditable.
    """

    model_config = ConfigDict(frozen=True)

    event_id: UUID = Field(default_factory=uuid.uuid4)
    event_type: str
    target_node_id: UUID | None = None
    target_edge_id: UUID | None = None
    # The original operation, serialised as JSONB in the DB
    operation_data: dict[str, Any]
    triggered_by: list[UUID] = Field(default_factory=list)
    reasoning: str | None = None
    confidence: str | None = None
    # content_timestamp from the originating signal; the replay-mode anchor
    content_timestamp: datetime
    # source: anchoring | disconfirmation | operator_override | seed
    source: str = "anchoring"
    rejected: bool = False
    rejection_reason: str | None = None


# ── Signal model (Tier A) ─────────────────────────────────────────────────────


class Signal(BaseModel):
    """A structured signal as stored in Tier A."""

    model_config = ConfigDict(frozen=True)

    signal_id: UUID = Field(default_factory=uuid.uuid4)
    payload_id: UUID
    lens_id: str
    lens_version: str = "1"
    claim_text: str
    # claim_vector omitted here — stored as pgvector in DB, not in Pydantic
    confidence_band: ConfidenceBand
    proposed_anchors: list[dict[str, Any]] = Field(default_factory=list)
    reasoning: str | None = None
    content_timestamp: datetime
    extracted_at: datetime


# ── Applier result ────────────────────────────────────────────────────────────


class ApplierResult(BaseModel):
    """Summary of what the Applier did with a batch of anchor operations."""

    applied: list[GraphUpdateEvent] = Field(default_factory=list)
    rejected: list[GraphUpdateEvent] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.applied) + len(self.rejected)

    @property
    def rejection_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return len(self.rejected) / self.total
