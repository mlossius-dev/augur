"""Data models for the projection layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ProbabilityBand(StrEnum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    NEGLIGIBLE = "negligible"


@dataclass
class GraphEvidence:
    """Snapshot of graph evidence used to seed scenario generation."""
    dimension: str | None
    active_conditions: list[dict]      # {node_id, name, description}
    strong_edges: list[dict]           # {edge_id, source_name, target_name, edge_type, weight_band}
    recent_changes: list[dict]         # {summary, change_type, occurred_at}


@dataclass
class Scenario:
    """One generated scenario."""
    scenario_id: str
    dimension: str | None
    title: str
    summary: str
    probability_band: ProbabilityBand
    time_horizon: str
    key_condition_ids: list[str]
    supporting_edge_ids: list[str]
    contradicting_edge_ids: list[str]
    generated_at: str
    as_of: str
    model_used: str | None = None
    deprecated: bool = False


@dataclass
class ProjectionResult:
    """Output of one projection run."""
    dimension: str | None
    scenarios: list[Scenario]
    n_conditions_used: int
    n_edges_used: int
    model_used: str
    as_of: str
