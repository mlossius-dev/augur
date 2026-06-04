"""
Build graph evidence bundles for scenario generation.

Collects active condition nodes, strong edges, and recent changes for a
given dimension (or all dimensions if dimension=None).  The evidence bundle
is passed to the LLM as structured context.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import asyncpg
import structlog

from augur.presentation.dimensions import DIMENSION_KEYWORDS
from augur.projection.models import GraphEvidence

log = structlog.get_logger(__name__)

# How many items to feed into the LLM — keeps prompts manageable
_MAX_CONDITIONS = 20
_MAX_EDGES = 25
_MAX_CHANGES = 10


async def gather_evidence(
    pool: asyncpg.Pool,
    *,
    dimension: str | None = None,
    as_of: datetime | None = None,
) -> GraphEvidence:
    """
    Collect active conditions, strong edges, and recent changes for one
    dimension (or all if dimension is None).
    """
    cutoff = as_of or datetime.now(timezone.utc)
    window_start = cutoff - timedelta(days=14)

    async with pool.acquire() as conn:
        # Active condition nodes
        condition_rows = await conn.fetch(
            """
            SELECT n.node_id, n.name, n.description,
                   n.type_data->>'current_state' AS current_state
            FROM nodes n
            WHERE n.node_type = 'condition'
              AND n.type_data->>'current_state' = 'active'
              AND n.created_at <= $1
            ORDER BY n.updated_at DESC
            LIMIT 100
            """,
            cutoff,
        )

        # Strong/moderate non-deprecated edges
        edge_rows = await conn.fetch(
            """
            SELECT e.edge_id, e.edge_type, e.current_weight_band,
                   sn.name AS source_name, tn.name AS target_name,
                   sn.node_type AS source_type, tn.node_type AS target_type
            FROM edges e
            JOIN nodes sn ON sn.node_id = e.source_node_id
            JOIN nodes tn ON tn.node_id = e.target_node_id
            WHERE e.current_weight_band IN ('strong', 'moderate')
              AND NOT e.deprecated
              AND e.created_at <= $1
            ORDER BY
                CASE e.current_weight_band WHEN 'strong' THEN 0 ELSE 1 END,
                e.updated_at DESC
            LIMIT 100
            """,
            cutoff,
        )

        # Recent changes (last 14 days)
        change_rows = await conn.fetch(
            """
            SELECT ewh.change_type, ewh.content_timestamp,
                   sn.name AS source_name, tn.name AS target_name,
                   e.edge_type
            FROM edge_weight_history ewh
            JOIN edges e ON e.edge_id = ewh.edge_id
            JOIN nodes sn ON sn.node_id = e.source_node_id
            JOIN nodes tn ON tn.node_id = e.target_node_id
            WHERE ewh.content_timestamp BETWEEN $1 AND $2
            ORDER BY ewh.content_timestamp DESC
            LIMIT 30
            """,
            window_start,
            cutoff,
        )

    # Filter by dimension if requested
    def _matches_dim(text: str) -> bool:
        if dimension is None:
            return True
        kws = DIMENSION_KEYWORDS.get(dimension, [])
        return any(kw in text.lower() for kw in kws)

    active_conditions = []
    for r in condition_rows:
        text = f"{r['name']} {r['description'] or ''}"
        if _matches_dim(text):
            active_conditions.append({
                "node_id": str(r["node_id"]),
                "name": r["name"],
                "description": r["description"] or "",
            })
    active_conditions = active_conditions[:_MAX_CONDITIONS]

    strong_edges = []
    for r in edge_rows:
        text = f"{r['source_name']} {r['target_name']}"
        if _matches_dim(text):
            strong_edges.append({
                "edge_id": str(r["edge_id"]),
                "source_name": r["source_name"],
                "target_name": r["target_name"],
                "edge_type": r["edge_type"],
                "weight_band": r["current_weight_band"],
            })
    strong_edges = strong_edges[:_MAX_EDGES]

    recent_changes = []
    for r in change_rows:
        verb = {"strengthened": "strengthened", "weakened": "weakened",
                "disconfirmation": "disconfirmed"}.get(r["change_type"], r["change_type"])
        recent_changes.append({
            "summary": f"{r['source_name']} {r['edge_type'].replace('_',' ')} {r['target_name']} — {verb}",
            "change_type": r["change_type"],
            "occurred_at": r["content_timestamp"].isoformat(),
        })
    recent_changes = recent_changes[:_MAX_CHANGES]

    return GraphEvidence(
        dimension=dimension,
        active_conditions=active_conditions,
        strong_edges=strong_edges,
        recent_changes=recent_changes,
    )
