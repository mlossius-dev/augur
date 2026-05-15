"""
Retrieve graph context relevant to a user question.

Uses pg_trgm trigram similarity to find nodes whose names/descriptions
match the question text, then pulls their connected edges and recent
signals.  The result is a structured evidence bundle passed to the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import asyncpg
import structlog

log = structlog.get_logger(__name__)

_MAX_NODES = 12
_MAX_EDGES = 15
_MAX_SIGNALS = 10
_TRGM_THRESHOLD = 0.1  # lower = broader match


@dataclass
class ConversationContext:
    """Graph evidence retrieved for a single user question."""
    question: str
    matched_nodes: list[dict]    # {node_id, name, node_type, current_state, description}
    connected_edges: list[dict]  # {edge_id, source_name, edge_type, target_name, weight_band, reasoning}
    recent_signals: list[dict]   # {claim_text, lens_id, confidence_band, content_timestamp}
    dimension_summary: list[dict] = field(default_factory=list)  # [{dimension, state, direction, active, total}]


async def retrieve_context(
    pool: asyncpg.Pool,
    question: str,
    *,
    as_of: datetime | None = None,
) -> ConversationContext:
    """
    Build a ConversationContext from graph evidence relevant to question.
    """
    cutoff = as_of or datetime.now(timezone.utc)
    signal_window = cutoff - timedelta(days=30)

    async with pool.acquire() as conn:
        # Trigram similarity search on nodes
        node_rows = await conn.fetch(
            """
            SELECT n.node_id, n.name, n.node_type, n.description,
                   n.type_data->>'current_state' AS current_state,
                   greatest(
                       similarity(n.name, $1),
                       similarity(coalesce(n.description, ''), $1)
                   ) AS score
            FROM nodes n
            WHERE n.created_at <= $2
              AND (
                  similarity(n.name, $1) > $3
                  OR similarity(coalesce(n.description, ''), $1) > $3
                  OR n.name ILIKE $4
              )
            ORDER BY score DESC
            LIMIT $5
            """,
            question,
            cutoff,
            _TRGM_THRESHOLD,
            f"%{question[:40]}%",
            _MAX_NODES,
        )

        node_ids = [r["node_id"] for r in node_rows]

        # Connected edges for matched nodes
        edge_rows = []
        if node_ids:
            edge_rows = await conn.fetch(
                """
                SELECT e.edge_id, e.edge_type, e.current_weight_band,
                       e.reasoning,
                       sn.name AS source_name, tn.name AS target_name
                FROM edges e
                JOIN nodes sn ON sn.node_id = e.source_node_id
                JOIN nodes tn ON tn.node_id = e.target_node_id
                WHERE (e.source_node_id = ANY($1) OR e.target_node_id = ANY($1))
                  AND NOT e.deprecated
                  AND e.created_at <= $2
                ORDER BY
                    CASE e.current_weight_band
                        WHEN 'strong' THEN 0 WHEN 'moderate' THEN 1
                        WHEN 'weak' THEN 2 ELSE 3
                    END,
                    e.updated_at DESC
                LIMIT $3
                """,
                node_ids,
                cutoff,
                _MAX_EDGES,
            )

        # Recent signals referencing matched node terms
        signal_rows = []
        if node_rows:
            name_terms = [r["name"][:30] for r in node_rows[:5]]
            like_expr = " OR ".join(f"s.claim_text ILIKE $${i + 3}" for i, _ in enumerate(name_terms))
            if like_expr:
                params = [signal_window, cutoff] + [f"%{t}%" for t in name_terms]
                signal_rows = await conn.fetch(
                    f"""
                    SELECT s.claim_text, s.lens_id, s.confidence_band, s.content_timestamp
                    FROM signals s
                    WHERE s.content_timestamp BETWEEN $1 AND $2
                      AND ({like_expr})
                    ORDER BY s.content_timestamp DESC
                    LIMIT {_MAX_SIGNALS}
                    """,
                    *params,
                )

        # Dimension overview (always included for context)
        dim_rows = await conn.fetch(
            """
            SELECT n.type_data->>'dimension' AS dimension,
                   COUNT(*) AS total,
                   SUM(CASE WHEN n.type_data->>'current_state' = 'active' THEN 1 ELSE 0 END) AS active
            FROM nodes n
            WHERE n.node_type = 'condition'
              AND n.created_at <= $1
            GROUP BY 1
            """,
            cutoff,
        )

    matched_nodes = [
        {
            "node_id": str(r["node_id"]),
            "name": r["name"],
            "node_type": r["node_type"],
            "current_state": r["current_state"],
            "description": r["description"] or "",
        }
        for r in node_rows
    ]

    connected_edges = [
        {
            "edge_id": str(r["edge_id"]),
            "source_name": r["source_name"],
            "edge_type": r["edge_type"],
            "target_name": r["target_name"],
            "weight_band": r["current_weight_band"],
            "reasoning": (r["reasoning"] or "")[:200],
        }
        for r in edge_rows
    ]

    recent_signals = [
        {
            "claim_text": r["claim_text"][:300],
            "lens_id": r["lens_id"],
            "confidence_band": r["confidence_band"],
            "content_timestamp": r["content_timestamp"].isoformat(),
        }
        for r in signal_rows
    ]

    dimension_summary = [
        {
            "dimension": r["dimension"] or "unclassified",
            "total": r["total"],
            "active": r["active"],
        }
        for r in dim_rows
        if r["dimension"]
    ]

    log.debug(
        "conversation.context_built",
        n_nodes=len(matched_nodes),
        n_edges=len(connected_edges),
        n_signals=len(recent_signals),
    )

    return ConversationContext(
        question=question,
        matched_nodes=matched_nodes,
        connected_edges=connected_edges,
        recent_signals=recent_signals,
        dimension_summary=dimension_summary,
    )
