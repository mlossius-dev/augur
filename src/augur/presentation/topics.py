"""
Topic view: operator-curated named clusters of graph nodes.

Topics are the bridge between the raw graph and human comprehension.
An operator assigns nodes to a topic; this module aggregates them into
a structured topic summary suitable for the presentation layer.
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
                       AS active_count
            FROM topics t
            LEFT JOIN topic_nodes tn ON tn.topic_id = t.topic_id
            LEFT JOIN nodes n ON n.node_id = tn.node_id
            GROUP BY t.topic_id
            ORDER BY t.name
            """
        )

    return [
        TopicSummary(
            topic_id=str(row["topic_id"]),
            name=row["name"],
            description=row["description"] or "",
            dimension=row["dimension"],
            node_count=row["node_count"],
            active_condition_count=row["active_count"],
            state=_derive_topic_state(row["active_count"], row["node_count"]),
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
            attention=_derive_attention(row["active_count"], row["node_count"]),
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
            "SELECT * FROM topics WHERE topic_id = $1",
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
    active = sum(1 for n in node_summaries if n.current_state == "active")

    return TopicDetail(
        topic_id=str(topic_row["topic_id"]),
        name=topic_row["name"],
        description=topic_row["description"] or "",
        dimension=topic_row["dimension"],
        node_count=total,
        active_condition_count=active,
        state=_derive_topic_state(active, total),
        created_at=topic_row["created_at"].isoformat(),
        updated_at=topic_row["updated_at"].isoformat(),
        attention=_derive_attention(active, total),
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


def _derive_topic_state(active: int, total: int) -> str:
    band = _compute_state_band(active, total)
    return str(band)


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


def _derive_attention(active: int, total: int) -> str:
    """Map the topic's state band to a high/medium/low attention tier."""
    return _ATTENTION_BY_BAND.get(_derive_topic_state(active, total), "low")
