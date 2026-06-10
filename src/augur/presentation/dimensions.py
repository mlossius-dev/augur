"""
Five-dimension scoring for the Augur home view (level 1 and level 2).

The five dimensions:
  1. economic_stability   — capital markets, monetary systems, banking, employment
  2. geopolitical_tension — state relations, conflict, alliances, diplomatic friction
  3. resource_availability — energy, food, water, critical materials
  4. environmental_stress — climate, weather extremes, natural disasters
  5. structural_change    — technology, demographics, institutional capacity

Each dimension is scored by:
  - Counting active Condition nodes whose names/descriptions match the dimension
  - Aggregating edge weight distributions for those conditions
  - Computing a state band (improving → crisis) and direction indicator

The state bands and direction are ordinal, not probabilistic, per the design
principles in augur-presentation.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

import asyncpg
import structlog

log = structlog.get_logger(__name__)

# ── Dimension definitions ──────────────────────────────────────────────────────

DIMENSIONS = [
    "economic_stability",
    "geopolitical_tension",
    "resource_availability",
    "environmental_stress",
    "structural_change",
]

DIMENSION_LABELS = {
    "economic_stability": "Economic Stability",
    "geopolitical_tension": "Geopolitical Tension",
    "resource_availability": "Resource Availability",
    "environmental_stress": "Environmental Stress",
    "structural_change": "Structural Change",
}

# Keyword lists for heuristic dimension assignment
# A node matches a dimension if any keyword appears in name or description (case-insensitive)
DIMENSION_KEYWORDS: dict[str, list[str]] = {
    "economic_stability": [
        "bank", "credit", "gdp", "inflation", "interest rate", "dollar", "euro",
        "yield", "bond", "equity", "market", "currency", "monetary", "fiscal",
        "employment", "recession", "debt", "capital", "finance", "lending",
        "rate", "economy", "economic", "treasury", "fed ", "ecb ", "imf ",
        "gold reserve", "reserve currency", "payment",
    ],
    "geopolitical_tension": [
        "sanction", "conflict", "war", "military", "alliance", "treaty", "election",
        "political", "government", "diplomacy", "border", "territory", "nuclear",
        "coup", "invasion", "escalation", "ceasefire", "nato", "un ", "g7", "g20",
        "arms", "weapon", "troop", "occupation", "sovereign",
    ],
    "resource_availability": [
        "oil", "gas", "energy", "supply chain", "food", "grain", "fertilizer",
        "commodity", "production", "export", "import", "pipeline", "refinery",
        "crude", "lng", "opec", "wheat", "corn", "soy", "coal", "mining",
        "shipping", "port", "freight", "storage", "inventory", "harvest",
    ],
    "environmental_stress": [
        "earthquake", "flood", "drought", "temperature", "climate", "wildfire",
        "hurricane", "disaster", "storm", "sea level", "emission", "carbon",
        "warming", "deforestation", "glacier", "fire", "extreme weather",
        "pollution", "water stress", "volcanic",
    ],
    "structural_change": [
        "regulation", "law", "legislation", "policy", "reform", "technology",
        "demographic", "infrastructure", "institutional", "digital", "ai ",
        "automation", "demographic", "urbanization", "migration", "population",
        "structural", "geopolitical shift",
    ],
}


class StateBand(StrEnum):
    IMPROVING = "improving"
    STABLE = "stable"
    STRAINED = "strained"
    DETERIORATING = "deteriorating"
    CRISIS = "crisis"
    UNKNOWN = "unknown"


class Direction(StrEnum):
    IMPROVING = "improving"
    STEADY = "steady"
    WORSENING = "worsening"
    UNKNOWN = "unknown"


@dataclass
class SparkPoint:
    """One data point in the sparkline (weekly bucket)."""
    week_start: str  # ISO date
    active_count: int
    total_count: int


@dataclass
class DimensionScore:
    """Aggregated score for one of the five dimensions."""
    dimension: str
    label: str
    state: StateBand
    direction: Direction
    active_conditions: int
    total_conditions: int
    strong_edge_count: int
    weak_edge_count: int
    sparkline: list[SparkPoint] = field(default_factory=list)
    rate: float = 0.0              # 1st derivative of the active-share sparkline (share/wk)
    rate_label: str = "unknown"    # human label, e.g. "moderate rising"
    acceleration: float = 0.0      # change in slope between window halves (share/wk²)
    accel_label: str = "unknown"   # "accelerating" | "decelerating" | "linear"
    notes: str = ""


# ── Scoring logic ──────────────────────────────────────────────────────────────


async def compute_dimension_scores(
    pool: asyncpg.Pool,
    *,
    as_of: datetime | None = None,
) -> list[DimensionScore]:
    """
    Compute scores for all five dimensions from current graph state.

    Uses keyword heuristics on condition node names/descriptions to assign
    nodes to dimensions. As the graph grows, this becomes more meaningful.
    """
    cutoff = as_of or datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        # Load all non-deprecated condition nodes created before as_of
        condition_rows = await conn.fetch(
            """
            SELECT n.node_id, n.name, n.description,
                   n.type_data->>'current_state' AS current_state
            FROM nodes n
            WHERE n.node_type = 'condition'
              AND n.created_at <= $1
            ORDER BY n.name
            """,
            cutoff,
        )

        # Count strong/moderate edges per condition node
        edge_rows = await conn.fetch(
            """
            SELECT source_node_id, target_node_id, current_weight_band
            FROM edges
            WHERE NOT deprecated
              AND created_at <= $1
            """,
            cutoff,
        )

        # Recent condition state changes (for direction — last 14 days vs 15-28 days ago)
        recent_changes = await conn.fetch(
            """
            SELECT csh.node_id,
                   csh.new_state,
                   csh.content_timestamp
            FROM condition_state_history csh
            WHERE csh.content_timestamp BETWEEN $1 AND $2
            ORDER BY csh.content_timestamp DESC
            """,
            cutoff - timedelta(days=28),
            cutoff,
        )

        # Sparkline: weekly condition activity over last 13 weeks (~91 days)
        sparkline_rows = await conn.fetch(
            """
            SELECT
                date_trunc('week', csh.content_timestamp) AS week_start,
                csh.node_id,
                csh.new_state
            FROM condition_state_history csh
            WHERE csh.content_timestamp >= $1
              AND csh.content_timestamp <= $2
            ORDER BY week_start
            """,
            cutoff - timedelta(days=91),
            cutoff,
        )

    # Build node→dimension map using keyword heuristics
    node_dimensions: dict[str, list[str]] = {}
    for row in condition_rows:
        text = f"{row['name']} {row['description'] or ''}".lower()
        matched = [
            dim for dim, keywords in DIMENSION_KEYWORDS.items()
            if any(kw in text for kw in keywords)
        ]
        if not matched:
            # Assign to structural_change as catch-all for unclassified conditions
            matched = ["structural_change"]
        node_dimensions[str(row["node_id"])] = matched

    # Build edge count maps (node → strong/weak counts)
    node_strong_edges: dict[str, int] = {}
    node_weak_edges: dict[str, int] = {}
    for r in edge_rows:
        for nid in [str(r["source_node_id"]), str(r["target_node_id"])]:
            if r["current_weight_band"] in ("strong", "moderate"):
                node_strong_edges[nid] = node_strong_edges.get(nid, 0) + 1
            else:
                node_weak_edges[nid] = node_weak_edges.get(nid, 0) + 1

    # Split condition changes into recent (0-14d) and prior (15-28d) windows
    recent_node_states: dict[str, str] = {}
    prior_node_states: dict[str, str] = {}
    recent_cutoff = cutoff - timedelta(days=14)
    for r in recent_changes:
        nid = str(r["node_id"])
        ts = r["content_timestamp"]
        if ts >= recent_cutoff:
            if nid not in recent_node_states:
                recent_node_states[nid] = r["new_state"]
        else:
            if nid not in prior_node_states:
                prior_node_states[nid] = r["new_state"]

    # Build sparkline data per dimension
    spark_by_dim: dict[str, dict[str, dict[str, int]]] = {d: {} for d in DIMENSIONS}
    for r in sparkline_rows:
        week_key = r["week_start"].strftime("%Y-%m-%d") if r["week_start"] else "unknown"
        nid = str(r["node_id"])
        dims = node_dimensions.get(nid, ["structural_change"])
        for dim in dims:
            if week_key not in spark_by_dim[dim]:
                spark_by_dim[dim][week_key] = {"active": 0, "total": 0}
            spark_by_dim[dim][week_key]["total"] += 1
            if r["new_state"] == "active":
                spark_by_dim[dim][week_key]["active"] += 1

    # Aggregate per dimension
    scores: list[DimensionScore] = []

    for dim in DIMENSIONS:
        # Collect condition nodes for this dimension
        dim_nodes = {
            str(r["node_id"]): r
            for r in condition_rows
            if dim in node_dimensions.get(str(r["node_id"]), [])
        }

        total = len(dim_nodes)
        active = sum(
            1 for r in dim_nodes.values()
            if r["current_state"] == "active"
        )
        strong = sum(node_strong_edges.get(nid, 0) for nid in dim_nodes)
        weak = sum(node_weak_edges.get(nid, 0) for nid in dim_nodes)

        state = _compute_state_band(active, total)
        direction = _compute_direction(dim_nodes, recent_node_states, prior_node_states)

        # Build sparkline
        spark_weeks = sorted(spark_by_dim[dim].keys())
        sparkline = [
            SparkPoint(
                week_start=wk,
                active_count=spark_by_dim[dim][wk]["active"],
                total_count=spark_by_dim[dim][wk]["total"],
            )
            for wk in spark_weeks
        ]

        rate, rate_label, accel, accel_label = _compute_velocity(sparkline)

        scores.append(DimensionScore(
            dimension=dim,
            label=DIMENSION_LABELS[dim],
            state=state,
            direction=direction,
            active_conditions=active,
            total_conditions=total,
            strong_edge_count=strong,
            weak_edge_count=weak,
            sparkline=sparkline,
            rate=rate,
            rate_label=rate_label,
            acceleration=accel,
            accel_label=accel_label,
        ))

    return scores


def _linfit_slope(ys: list[float]) -> float:
    """Least-squares slope of ys against its index (0..n-1)."""
    n = len(ys)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(ys) / n
    denom = sum((i - mean_x) ** 2 for i in range(n))
    if denom == 0:
        return 0.0
    num = sum((i - mean_x) * (ys[i] - mean_y) for i in range(n))
    return num / denom


def _compute_velocity(sparkline: list[SparkPoint]) -> tuple[float, str, float, str]:
    """
    Derive rate (1st derivative) and acceleration (2nd derivative) from the
    weekly active-share series the user actually sees on the sparkline.

    Rate is the least-squares slope over the last six valid weeks, in units of
    active-share per week. A *rising* share means more conditions active = more
    stress = worsening, so the polarity is folded into the label.

    Acceleration compares the slope of the recent half of the window against the
    prior half: a steepening trend (|slope| growing) reads as "accelerating".

    The sparkline is sparse (only weeks with state changes carry data), so we
    guard on a minimum of three valid points and return honest "insufficient
    data" otherwise.
    """
    ratios = [sp.active_count / sp.total_count for sp in sparkline if sp.total_count > 0]
    if len(ratios) < 3:
        return 0.0, "insufficient data", 0.0, "—"

    window = ratios[-6:]
    rate = _linfit_slope(window)

    mag = abs(rate)
    if mag < 0.005:
        rate_label = "steady"
    else:
        size = "gentle" if mag < 0.02 else "moderate" if mag < 0.05 else "steep"
        rate_label = f"{size} {'rising' if rate > 0 else 'easing'}"

    if len(window) >= 4:
        half = len(window) // 2
        slope_prior = _linfit_slope(window[:len(window) - half])
        slope_recent = _linfit_slope(window[half:])
        accel = slope_recent - slope_prior
        steepening = abs(slope_recent) - abs(slope_prior)
        if abs(steepening) < 0.005:
            accel_label = "linear"
        else:
            accel_label = "accelerating" if steepening > 0 else "decelerating"
    else:
        accel = 0.0
        accel_label = "—"

    return round(rate, 4), rate_label, round(accel, 4), accel_label


def _compute_state_band(active: int, total: int) -> StateBand:
    """Compute state band from active condition ratio."""
    if total == 0:
        return StateBand.UNKNOWN
    ratio = active / total
    if ratio < 0.20:
        return StateBand.IMPROVING
    if ratio < 0.40:
        return StateBand.STABLE
    if ratio < 0.60:
        return StateBand.STRAINED
    if ratio < 0.80:
        return StateBand.DETERIORATING
    return StateBand.CRISIS


def _compute_direction(
    dim_nodes: dict[str, Any],
    recent: dict[str, str],
    prior: dict[str, str],
) -> Direction:
    """
    Compare recent (0-14d) active count vs prior (15-28d) to determine direction.
    """
    if not dim_nodes:
        return Direction.UNKNOWN

    recent_active = sum(1 for nid in dim_nodes if recent.get(nid) == "active")
    prior_active = sum(1 for nid in dim_nodes if prior.get(nid) == "active")

    if not recent and not prior:
        return Direction.UNKNOWN

    delta = recent_active - prior_active
    if delta > 1:
        return Direction.WORSENING
    if delta < -1:
        return Direction.IMPROVING
    return Direction.STEADY
