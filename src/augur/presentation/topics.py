"""
Topic view: named clusters of graph nodes.

Topics are the bridge between the raw graph and human comprehension.
They emerge from daily graph clustering (augur.presentation.clustering)
and are curated by the operator; this module aggregates them into a
structured topic summary suitable for the presentation layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import asyncpg
import structlog

from augur.presentation.dimensions import (
    DIMENSION_LABELS,
    _compute_state_band,
)

log = structlog.get_logger(__name__)


@dataclass
class TopicNodeSummary:
    node_id: str
    name: str
    node_type: str
    current_state: str | None  # only set for condition nodes
    added_at: str
    notes: str


@dataclass
class TopicSummary:
    """Summary of a single topic — used in list views."""
    topic_id: str
    name: str
    description: str
    dimension: str | None
    node_count: int
    active_condition_count: int
    state: str  # derived from active conditions
    created_at: str
    updated_at: str
    attention: str = "low"  # priority proxy: high | medium | low (severity-first)
    edge_count: int = 0      # live edges internal to the topic subgraph (both endpoints in topic)


@dataclass
class TopicDetail(TopicSummary):
    """Full topic detail including member nodes."""
    nodes: list[TopicNodeSummary] = field(default_factory=list)


async def get_topic_list(pool: asyncpg.Pool) -> list[TopicSummary]:
    """Return all topics with lightweight aggregated stats."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.topic_id, t.name, t.description, t.dimension,
                   t.created_at, t.updated_at,
                   COUNT(tn.node_id) AS node_count,
                   COUNT(CASE WHEN n.type_data->>'current_state' = 'active' THEN 1 END)
                       AS active_count,
                   COUNT(CASE WHEN n.node_type = 'condition' THEN 1 END) AS condition_count,
                   MAX(b.graph_active) AS graph_active,
                   MAX(b.graph_conditions) AS graph_conditions
            FROM topics t
            LEFT JOIN topic_nodes tn ON tn.topic_id = t.topic_id
            LEFT JOIN nodes n ON n.node_id = tn.node_id
            CROSS JOIN (""" + _BASELINE_SQL + """) b
            WHERE NOT t.hidden
            GROUP BY t.topic_id
            -- Largest first; the home page orders by the last 24 hours' changes
            ORDER BY COUNT(tn.node_id) DESC, t.name
            """
        )

        # Internal edge counts: live edges with BOTH endpoints in the same topic.
        edge_rows = await conn.fetch(
            """
            SELECT tn_s.topic_id, COUNT(DISTINCT e.edge_id) AS edge_count
            FROM edges e
            JOIN topic_nodes tn_s ON tn_s.node_id = e.source_node_id
            JOIN topic_nodes tn_t ON tn_t.node_id = e.target_node_id
                                  AND tn_t.topic_id = tn_s.topic_id
            WHERE NOT e.deprecated
            GROUP BY tn_s.topic_id
            """
        )

    edge_counts = {str(r["topic_id"]): int(r["edge_count"]) for r in edge_rows}

    return [
        TopicSummary(
            topic_id=str(row["topic_id"]),
            name=row["name"],
            description=row["description"] or "",
            dimension=row["dimension"],
            node_count=row["node_count"],
            active_condition_count=row["active_count"],
            # Active share of the topic's conditions against the whole graph's
            state=_derive_topic_state(row["active_count"], row["condition_count"], _baseline(row)),
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
            attention=_derive_attention(row["active_count"], row["condition_count"], _baseline(row)),
            edge_count=edge_counts.get(str(row["topic_id"]), 0),
        )
        for row in rows
    ]


async def get_topic_detail(
    pool: asyncpg.Pool,
    topic_id: str,
    *,
    as_of: datetime | None = None,
) -> TopicDetail | None:
    """Return full topic detail with member nodes."""
    cutoff = as_of or datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        topic_row = await conn.fetchrow(
            "SELECT t.*, b.graph_active, b.graph_conditions FROM topics t "
            f"CROSS JOIN ({_BASELINE_SQL}) b WHERE t.topic_id = $1",
            topic_id,
        )
        if not topic_row:
            return None

        node_rows = await conn.fetch(
            """
            SELECT n.node_id, n.name, n.node_type,
                   n.type_data->>'current_state' AS current_state,
                   tn.added_at, tn.notes
            FROM topic_nodes tn
            JOIN nodes n ON n.node_id = tn.node_id
            WHERE tn.topic_id = $1
              AND n.created_at <= $2
            ORDER BY n.name
            """,
            topic_id,
            cutoff,
        )

        # Internal subgraph edges: both endpoints among this topic's nodes.
        node_ids = [r["node_id"] for r in node_rows]
        edge_count = 0
        if node_ids:
            edge_count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM edges e
                WHERE NOT e.deprecated
                  AND e.created_at <= $2
                  AND e.source_node_id = ANY($1::uuid[])
                  AND e.target_node_id = ANY($1::uuid[])
                """,
                node_ids,
                cutoff,
            )

    node_summaries = [
        TopicNodeSummary(
            node_id=str(r["node_id"]),
            name=r["name"],
            node_type=r["node_type"],
            current_state=r["current_state"],
            added_at=r["added_at"].isoformat() if r["added_at"] else "",
            notes=r["notes"] or "",
        )
        for r in node_rows
    ]

    total = len(node_summaries)
    conditions = sum(1 for n in node_summaries if n.node_type == "condition")
    active = sum(1 for n in node_summaries if n.current_state == "active")

    return TopicDetail(
        topic_id=str(topic_row["topic_id"]),
        name=topic_row["name"],
        description=topic_row["description"] or "",
        dimension=topic_row["dimension"],
        node_count=total,
        active_condition_count=active,
        state=_derive_topic_state(active, conditions, _baseline(topic_row)),
        created_at=topic_row["created_at"].isoformat(),
        updated_at=topic_row["updated_at"].isoformat(),
        attention=_derive_attention(active, conditions, _baseline(topic_row)),
        edge_count=edge_count,
        nodes=node_summaries,
    )


async def create_topic(
    pool: asyncpg.Pool,
    *,
    name: str,
    description: str = "",
    dimension: str | None = None,
) -> str:
    """Create a new topic. Returns the new topic_id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO topics (name, description, dimension)
            VALUES ($1, $2, $3)
            RETURNING topic_id
            """,
            name,
            description,
            dimension,
        )
    return str(row["topic_id"])


async def assign_nodes_to_topic(
    pool: asyncpg.Pool,
    *,
    topic_id: str,
    node_ids: list[str],
    notes: str = "",
) -> int:
    """Add nodes to a topic. Returns count of newly inserted rows."""
    if not node_ids:
        return 0
    async with pool.acquire() as conn:
        result = await conn.executemany(
            """
            INSERT INTO topic_nodes (topic_id, node_id, notes)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            """,
            [(topic_id, nid, notes) for nid in node_ids],
        )
    # executemany returns a status string like "INSERT 0 N"
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):
        return len(node_ids)


async def remove_nodes_from_topic(
    pool: asyncpg.Pool,
    *,
    topic_id: str,
    node_ids: list[str],
) -> int:
    """Remove nodes from a topic. Returns count deleted."""
    if not node_ids:
        return 0
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM topic_nodes WHERE topic_id = $1 AND node_id = ANY($2::uuid[])",
            topic_id,
            node_ids,
        )
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):
        return 0


async def list_topics_for_node(pool: asyncpg.Pool, node_id: str) -> list[dict[str, Any]]:
    """Return all topics a given node belongs to."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT t.topic_id, t.name, t.description, t.dimension,
                   tn.added_at, tn.notes
            FROM topic_nodes tn
            JOIN topics t ON t.topic_id = tn.topic_id
            WHERE tn.node_id = $1
            ORDER BY t.name
            """,
            node_id,
        )
    return [dict(r) for r in rows]


# Conditions are created active and rarely deactivate, so most of the graph's
# conditions are active and a fixed share threshold calls every topic a crisis.
# A topic's band is how far its active share sits above or below the whole
# graph's, its share shrunk toward the graph's by TOPIC_BAND_PRIOR conditions
# so a topic with three conditions does not swing to an extreme
# (docs/augur-frontend-implementation.md, derived metrics; operator decision, October 2026).
TOPIC_BAND_PRIOR = 5
TOPIC_BAND_CUTS = ((-0.5, "improving"), (-0.15, "stable"), (0.15, "strained"), (0.5, "deteriorating"))

_BASELINE_SQL = """
    SELECT COUNT(*) FILTER (WHERE type_data->>'current_state' = 'active') AS graph_active,
           COUNT(*) AS graph_conditions
    FROM nodes WHERE node_type = 'condition' AND merged_into IS NULL
"""


def _baseline(row: Any) -> tuple[int, int] | None:
    """The graph's (active, conditions) from a row, when the query carried them."""
    try:
        active, total = row.get("graph_active"), row.get("graph_conditions")
    except AttributeError:
        return None
    return (int(active), int(total)) if active is not None and total else None


def _derive_topic_state(active: int, total: int, baseline: tuple[int, int] | None = None) -> str:
    """`total` is the topic's conditions; without a graph baseline, the fixed bands."""
    if total == 0:
        return "unknown"
    if baseline is None or not baseline[1]:
        return str(_compute_state_band(active, total))
    g_active, g_total = baseline
    base = g_active / g_total
    if base in (0.0, 1.0):
        return str(_compute_state_band(active, total))
    share = (active + base * TOPIC_BAND_PRIOR) / (total + TOPIC_BAND_PRIOR)
    lean = (share - base) / (1 - base) if share >= base else (share - base) / base
    for cut, band in TOPIC_BAND_CUTS:
        if lean < cut:
            return band
    return "crisis"


# Attention is a severity-first priority proxy over the same state band the
# topic already reports: a topic in a worse band warrants closer attention.
_ATTENTION_BY_BAND = {
    "crisis": "high",
    "deteriorating": "high",
    "strained": "medium",
    "stable": "low",
    "improving": "low",
    "unknown": "low",
}


def _derive_attention(active: int, total: int, baseline: tuple[int, int] | None = None) -> str:
    """Map the topic's state band to a high/medium/low attention tier."""
    return _ATTENTION_BY_BAND.get(_derive_topic_state(active, total, baseline), "low")
