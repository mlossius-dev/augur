"""
Home view level-3: "What changed in the last 24 hours."

Queries graph mutation tables (edge_weight_history, condition_state_history,
edges) and returns a ranked list of impactful changes for the home view.

Impact criteria (from augur-presentation.md):
  - A high-weight edge strengthened or weakened
  - A condition activated or deactivated
  - A new edge created with starting weight moderate or higher
  - A disconfirmation pass meaningfully weakened a previously high-weight edge
  - A new event node connected to active conditions

Changes are ranked by impact: weight changes on strong/moderate edges first,
condition activations/deactivations second, new edges third.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
import structlog

from augur.presentation.dimensions import DIMENSION_KEYWORDS, DIMENSIONS

log = structlog.get_logger(__name__)


@dataclass
class ChangeRecord:
    """One impactful graph change for the home view."""
    change_id: str              # Unique composite ID for this change record
    change_type: str            # edge_strengthened | edge_weakened | condition_activated |
                                #   condition_deactivated | edge_created | disconfirmation_weakened
    summary: str                # One-sentence description
    dimension: str              # Which of the five dimensions this affects
    dimension_label: str        # Human-readable label
    occurred_at: str            # ISO timestamp
    target_id: str              # UUID of the affected node or edge
    target_type: str            # "node" | "edge"
    target_name: str            # Human-readable name
    weight_before: str | None   # For edge changes: previous weight band
    weight_after: str | None    # For edge changes: new weight band
    impact_rank: int            # Lower = more impactful (for sorting)
    downstream_edge_count: int = 0  # 1-hop live edges incident to the affected node(s)


# Impact weights for sorting
_IMPACT = {
    "disconfirmation_weakened": 1,
    "edge_strengthened": 2,
    "edge_weakened": 3,
    "condition_activated": 4,
    "condition_deactivated": 5,
    "edge_created": 6,
}


async def get_recent_changes(
    pool: asyncpg.Pool,
    *,
    hours: int = 24,
    as_of: datetime | None = None,
    limit: int = 15,
) -> list[ChangeRecord]:
    """
    Return the most impactful graph changes in the lookback window.

    Changes come from three sources:
      1. edge_weight_history — strengthened/weakened edges
      2. condition_state_history — condition activations/deactivations
      3. edges (created_at) — newly created high-weight edges
    """
    cutoff = as_of or datetime.now(timezone.utc)
    window_start = cutoff - timedelta(hours=hours)

    changes: list[ChangeRecord] = []

    async with pool.acquire() as conn:
        # 1. Edge weight changes (strengthened / weakened)
        weight_rows = await conn.fetch(
            """
            SELECT ewh.id, ewh.edge_id, ewh.weight_band, ewh.previous_weight_band,
                   ewh.change_type, ewh.content_timestamp,
                   sn.name AS source_name, tn.name AS target_name,
                   e.edge_type
            FROM edge_weight_history ewh
            JOIN edges e ON e.edge_id = ewh.edge_id
            JOIN nodes sn ON sn.node_id = e.source_node_id
            JOIN nodes tn ON tn.node_id = e.target_node_id
            WHERE ewh.content_timestamp BETWEEN $1 AND $2
              AND ewh.change_type IN ('strengthened', 'weakened', 'disconfirmation')
              AND (ewh.weight_band IN ('strong', 'moderate')
                   OR ewh.previous_weight_band IN ('strong', 'moderate'))
              AND NOT e.deprecated
            ORDER BY ewh.content_timestamp DESC
            LIMIT 50
            """,
            window_start,
            cutoff,
        )

        # 2. Condition state changes (activated / deactivated)
        state_rows = await conn.fetch(
            """
            SELECT csh.id, csh.node_id, csh.new_state, csh.previous_state,
                   csh.content_timestamp, n.name AS node_name, n.description
            FROM condition_state_history csh
            JOIN nodes n ON n.node_id = csh.node_id
            WHERE csh.content_timestamp BETWEEN $1 AND $2
              AND csh.new_state IN ('active', 'inactive')
              AND (csh.previous_state IS NULL OR csh.previous_state != csh.new_state)
            ORDER BY csh.content_timestamp DESC
            LIMIT 50
            """,
            window_start,
            cutoff,
        )

        # 3. Newly created high-weight edges
        new_edge_rows = await conn.fetch(
            """
            SELECT e.edge_id, e.edge_type, e.current_weight_band, e.created_at,
                   sn.name AS source_name, tn.name AS target_name
            FROM edges e
            JOIN nodes sn ON sn.node_id = e.source_node_id
            JOIN nodes tn ON tn.node_id = e.target_node_id
            WHERE e.created_at BETWEEN $1 AND $2
              AND e.current_weight_band IN ('strong', 'moderate')
              AND NOT e.deprecated
            ORDER BY e.created_at DESC
            LIMIT 30
            """,
            window_start,
            cutoff,
        )

    # Build change records
    for row in weight_rows:
        change_type = (
            "disconfirmation_weakened" if row["change_type"] == "disconfirmation"
            else "edge_strengthened" if row["change_type"] == "strengthened"
            else "edge_weakened"
        )
        edge_name = f"{row['source_name']} {_edge_verb(row['edge_type'])} {row['target_name']}"
        dim = _infer_dimension(edge_name)
        changes.append(ChangeRecord(
            change_id=f"ewh-{row['id']}",
            change_type=change_type,
            summary=_weight_change_summary(change_type, edge_name, row["weight_band"]),
            dimension=dim,
            dimension_label=_dim_label(dim),
            occurred_at=row["content_timestamp"].isoformat(),
            target_id=str(row["edge_id"]),
            target_type="edge",
            target_name=edge_name,
            weight_before=row["previous_weight_band"],
            weight_after=row["weight_band"],
            impact_rank=_IMPACT.get(change_type, 9),
        ))

    for row in state_rows:
        change_type = (
            "condition_activated" if row["new_state"] == "active"
            else "condition_deactivated"
        )
        node_name = row["node_name"]
        dim = _infer_dimension(f"{node_name} {row.get('description') or ''}")
        changes.append(ChangeRecord(
            change_id=f"csh-{row['id']}",
            change_type=change_type,
            summary=_condition_summary(change_type, node_name),
            dimension=dim,
            dimension_label=_dim_label(dim),
            occurred_at=row["content_timestamp"].isoformat(),
            target_id=str(row["node_id"]),
            target_type="node",
            target_name=node_name,
            weight_before=None,
            weight_after=None,
            impact_rank=_IMPACT.get(change_type, 9),
        ))

    for row in new_edge_rows:
        edge_name = f"{row['source_name']} {_edge_verb(row['edge_type'])} {row['target_name']}"
        dim = _infer_dimension(edge_name)
        changes.append(ChangeRecord(
            change_id=f"edge-new-{row['edge_id']}",
            change_type="edge_created",
            summary=f"New {row['current_weight_band']} link established: {edge_name}.",
            dimension=dim,
            dimension_label=_dim_label(dim),
            occurred_at=row["created_at"].isoformat(),
            target_id=str(row["edge_id"]),
            target_type="edge",
            target_name=edge_name,
            weight_before=None,
            weight_after=row["current_weight_band"],
            impact_rank=_IMPACT["edge_created"],
        ))

    # Deduplicate (edge might appear in weight_history AND new_edge_rows)
    seen: set[str] = set()
    unique: list[ChangeRecord] = []
    for c in changes:
        if c.change_id not in seen:
            seen.add(c.change_id)
            unique.append(c)

    # Sort by impact rank, then recency
    unique.sort(key=lambda c: (c.impact_rank, c.occurred_at), reverse=False)
    result = unique[:limit]

    await _attach_downstream_counts(pool, result)
    return result


async def _attach_downstream_counts(
    pool: asyncpg.Pool,
    changes: list[ChangeRecord],
) -> None:
    """
    Populate ``downstream_edge_count`` on each change: the number of live edges
    in the immediate (1-hop) neighbourhood of the affected target.

    For a node target that is its edge degree. For an edge target it is the
    count of other live edges sharing either endpoint — the edge's local blast
    radius. Two batched queries over the (small) final change set; no traversal.
    """
    if not changes:
        return

    node_ids = [c.target_id for c in changes if c.target_type == "node"]
    edge_ids = [c.target_id for c in changes if c.target_type == "edge"]

    node_counts: dict[str, int] = {}
    edge_counts: dict[str, int] = {}

    async with pool.acquire() as conn:
        if node_ids:
            rows = await conn.fetch(
                """
                SELECT nid::text AS nid, COUNT(*) AS cnt FROM (
                    SELECT source_node_id AS nid FROM edges WHERE NOT deprecated
                    UNION ALL
                    SELECT target_node_id AS nid FROM edges WHERE NOT deprecated
                ) x
                WHERE nid = ANY($1::uuid[])
                GROUP BY nid
                """,
                node_ids,
            )
            node_counts = {r["nid"]: int(r["cnt"]) for r in rows}

        if edge_ids:
            rows = await conn.fetch(
                """
                SELECT e.edge_id::text AS eid,
                       (SELECT COUNT(*) FROM edges x
                        WHERE NOT x.deprecated
                          AND x.edge_id <> e.edge_id
                          AND (x.source_node_id IN (e.source_node_id, e.target_node_id)
                               OR x.target_node_id IN (e.source_node_id, e.target_node_id))
                       ) AS cnt
                FROM edges e
                WHERE e.edge_id = ANY($1::uuid[])
                """,
                edge_ids,
            )
            edge_counts = {r["eid"]: int(r["cnt"]) for r in rows}

    for c in changes:
        if c.target_type == "node":
            c.downstream_edge_count = node_counts.get(c.target_id, 0)
        else:
            c.downstream_edge_count = edge_counts.get(c.target_id, 0)


def _infer_dimension(text: str) -> str:
    """Assign a dimension based on keyword matching in a text snippet."""
    text_lower = text.lower()
    # Count keyword matches per dimension
    scores = {
        dim: sum(1 for kw in keywords if kw in text_lower)
        for dim, keywords in DIMENSION_KEYWORDS.items()
    }
    best = max(scores, key=lambda d: scores[d])
    return best if scores[best] > 0 else "structural_change"


def _dim_label(dim: str) -> str:
    from augur.presentation.dimensions import DIMENSION_LABELS
    return DIMENSION_LABELS.get(dim, dim.replace("_", " ").title())


def _edge_verb(edge_type: str) -> str:
    verbs = {
        "causes": "causes",
        "enables": "enables",
        "constrains": "constrains",
        "accelerates": "accelerates",
        "correlates_with": "correlates with",
        "contradicts": "contradicts",
        "refines": "refines",
        "part_of": "is part of",
        "produces": "produces",
    }
    return verbs.get(edge_type, edge_type.replace("_", " "))


def _weight_change_summary(change_type: str, edge_name: str, new_band: str) -> str:
    if change_type == "disconfirmation_weakened":
        return f"Disconfirmation weakened: {edge_name}."
    if change_type == "edge_strengthened":
        return f"Strengthened to {new_band}: {edge_name}."
    return f"Weakened to {new_band}: {edge_name}."


def _condition_summary(change_type: str, name: str) -> str:
    if change_type == "condition_activated":
        return f"Condition activated: {name}."
    return f"Condition deactivated: {name}."
