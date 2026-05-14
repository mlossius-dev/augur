"""
Reasoning view API endpoints (level 5 in the presentation hierarchy).

GET /api/reasoning/node/{node_id}  — full node detail for drilling in
GET /api/reasoning/edge/{edge_id}  — full edge detail for drilling in

Both support an as_of parameter for time-scrubber use.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/reasoning", tags=["reasoning"])


def _pool(request: Request):
    return request.app.state.raw_pool


def _as_of(as_of: str | None) -> datetime | None:
    if not as_of:
        return None
    try:
        return datetime.fromisoformat(as_of)
    except ValueError:
        return None


@router.get("/node/{node_id}")
async def reasoning_node(
    node_id: UUID,
    request: Request,
    as_of: str | None = Query(None),
) -> JSONResponse:
    """
    Full reasoning detail for a node.

    Returns:
      - Node metadata and type-specific data
      - All non-deprecated edges connected to this node
      - Signals that created this node (cite their sources)
      - For Condition nodes: recent state history
    """
    pool = _pool(request)
    ts = _as_of(as_of)
    cutoff = ts or datetime.utcnow()

    async with pool.acquire() as conn:
        node_row = await conn.fetchrow(
            """
            SELECT node_id, node_type, name, description, type_data,
                   created_from, langfuse_trace_ids, created_at, updated_at
            FROM nodes WHERE node_id = $1
            """,
            node_id,
        )
        if not node_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

        # Connected edges (as source or target)
        edge_rows = await conn.fetch(
            """
            SELECT e.edge_id, e.edge_type, e.current_weight_band,
                   e.reasoning, e.falsification_criteria,
                   e.supporting_signals, e.disconfirming_signals,
                   e.created_at, e.deprecated,
                   sn.node_id AS source_id, sn.name AS source_name, sn.node_type AS source_type,
                   tn.node_id AS target_id, tn.name AS target_name, tn.node_type AS target_type
            FROM edges e
            JOIN nodes sn ON sn.node_id = e.source_node_id
            JOIN nodes tn ON tn.node_id = e.target_node_id
            WHERE (e.source_node_id = $1 OR e.target_node_id = $1)
              AND e.created_at <= $2
              AND NOT e.deprecated
            ORDER BY e.current_weight_band DESC, e.created_at DESC
            """,
            node_id,
            cutoff,
        )

        # Signals that created this node (with source info from payloads)
        created_from = list(node_row["created_from"] or [])
        signal_rows = []
        if created_from:
            signal_rows = await conn.fetch(
                """
                SELECT s.signal_id, s.lens_id, s.claim_text, s.reasoning,
                       s.confidence_band, s.content_timestamp,
                       p.source_id
                FROM signals s
                LEFT JOIN payloads p ON p.payload_id = s.payload_id
                WHERE s.signal_id = ANY($1)
                ORDER BY s.content_timestamp DESC
                """,
                created_from,
            )

        # Condition state history (if condition node)
        state_history = []
        if node_row["node_type"] == "condition":
            state_history = await conn.fetch(
                """
                SELECT new_state, previous_state, reasoning, content_timestamp
                FROM condition_state_history
                WHERE node_id = $1
                  AND content_timestamp <= $2
                ORDER BY content_timestamp DESC
                LIMIT 20
                """,
                node_id,
                cutoff,
            )

    import json

    def safe_json(v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v

    return JSONResponse({
        "node": {
            "node_id": str(node_row["node_id"]),
            "node_type": node_row["node_type"],
            "name": node_row["name"],
            "description": node_row["description"],
            "type_data": safe_json(node_row["type_data"]),
            "created_at": node_row["created_at"].isoformat() if node_row["created_at"] else None,
            "langfuse_trace_ids": list(node_row["langfuse_trace_ids"] or []),
        },
        "edges": [
            {
                "edge_id": str(r["edge_id"]),
                "edge_type": r["edge_type"],
                "weight_band": r["current_weight_band"],
                "reasoning": r["reasoning"],
                "falsification_criteria": r["falsification_criteria"],
                "source": {"id": str(r["source_id"]), "name": r["source_name"], "type": r["source_type"]},
                "target": {"id": str(r["target_id"]), "name": r["target_name"], "type": r["target_type"]},
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in edge_rows
        ],
        "signals": [
            {
                "signal_id": str(r["signal_id"]),
                "lens_id": r["lens_id"],
                "source_id": r["source_id"],
                "claim_text": r["claim_text"],
                "confidence_band": r["confidence_band"],
                "content_timestamp": r["content_timestamp"].isoformat() if r["content_timestamp"] else None,
            }
            for r in signal_rows
        ],
        "state_history": [
            {
                "new_state": r["new_state"],
                "previous_state": r["previous_state"],
                "reasoning": r["reasoning"],
                "content_timestamp": r["content_timestamp"].isoformat() if r["content_timestamp"] else None,
            }
            for r in state_history
        ],
    })


@router.get("/edge/{edge_id}")
async def reasoning_edge(
    edge_id: UUID,
    request: Request,
    as_of: str | None = Query(None),
) -> JSONResponse:
    """
    Full reasoning detail for an edge.

    Returns:
      - Edge metadata, weight band, reasoning, falsification criteria
      - Source and target node summaries
      - Weight history (the time series of weight changes)
      - Supporting and disconfirming signals with source citations
    """
    pool = _pool(request)
    ts = _as_of(as_of)
    cutoff = ts or datetime.utcnow()

    async with pool.acquire() as conn:
        edge_row = await conn.fetchrow(
            """
            SELECT e.edge_id, e.edge_type, e.current_weight_band,
                   e.reasoning, e.falsification_criteria,
                   e.supporting_signals, e.disconfirming_signals,
                   e.last_disconfirmation_pass,
                   e.created_at, e.deprecated, e.deprecated_at,
                   e.langfuse_trace_ids,
                   sn.node_id AS source_id, sn.name AS source_name,
                   sn.node_type AS source_type, sn.description AS source_desc,
                   tn.node_id AS target_id, tn.name AS target_name,
                   tn.node_type AS target_type, tn.description AS target_desc
            FROM edges e
            JOIN nodes sn ON sn.node_id = e.source_node_id
            JOIN nodes tn ON tn.node_id = e.target_node_id
            WHERE e.edge_id = $1
              AND e.created_at <= $2
            """,
            edge_id,
            cutoff,
        )
        if not edge_row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Edge not found")

        # Weight history
        weight_history = await conn.fetch(
            """
            SELECT weight_band, previous_weight_band, change_type, reasoning,
                   content_timestamp
            FROM edge_weight_history
            WHERE edge_id = $1
              AND content_timestamp <= $2
            ORDER BY content_timestamp DESC
            LIMIT 30
            """,
            edge_id,
            cutoff,
        )

        # Supporting signals
        supporting_ids = list(edge_row["supporting_signals"] or [])
        supporting_rows = []
        if supporting_ids:
            supporting_rows = await conn.fetch(
                """
                SELECT s.signal_id, s.lens_id, s.claim_text, s.confidence_band,
                       s.content_timestamp, p.source_id
                FROM signals s
                LEFT JOIN payloads p ON p.payload_id = s.payload_id
                WHERE s.signal_id = ANY($1)
                ORDER BY s.content_timestamp DESC
                LIMIT 20
                """,
                supporting_ids,
            )

        # Disconfirming signals
        disconf_ids = list(edge_row["disconfirming_signals"] or [])
        disconf_rows = []
        if disconf_ids:
            disconf_rows = await conn.fetch(
                """
                SELECT s.signal_id, s.lens_id, s.claim_text, s.confidence_band,
                       s.content_timestamp, p.source_id
                FROM signals s
                LEFT JOIN payloads p ON p.payload_id = s.payload_id
                WHERE s.signal_id = ANY($1)
                ORDER BY s.content_timestamp DESC
                LIMIT 20
                """,
                disconf_ids,
            )

        # Disconfirmation pass events for this edge
        disconf_events = await conn.fetch(
            """
            SELECT pass_event_id, outcome, reasoning, challenged_at,
                   weight_band_at_challenge
            FROM disconfirmation_pass_events
            WHERE edge_id = $1
            ORDER BY challenged_at DESC
            LIMIT 10
            """,
            edge_id,
        )

    def _sig(r: Any) -> dict:
        return {
            "signal_id": str(r["signal_id"]),
            "lens_id": r["lens_id"],
            "source_id": r["source_id"],
            "claim_text": r["claim_text"],
            "confidence_band": r["confidence_band"],
            "content_timestamp": r["content_timestamp"].isoformat() if r["content_timestamp"] else None,
        }

    return JSONResponse({
        "edge": {
            "edge_id": str(edge_row["edge_id"]),
            "edge_type": edge_row["edge_type"],
            "weight_band": edge_row["current_weight_band"],
            "reasoning": edge_row["reasoning"],
            "falsification_criteria": edge_row["falsification_criteria"],
            "deprecated": edge_row["deprecated"],
            "last_disconfirmation_pass": edge_row["last_disconfirmation_pass"].isoformat() if edge_row["last_disconfirmation_pass"] else None,
            "created_at": edge_row["created_at"].isoformat() if edge_row["created_at"] else None,
            "langfuse_trace_ids": list(edge_row["langfuse_trace_ids"] or []),
        },
        "source_node": {
            "node_id": str(edge_row["source_id"]),
            "name": edge_row["source_name"],
            "node_type": edge_row["source_type"],
            "description": edge_row["source_desc"],
        },
        "target_node": {
            "node_id": str(edge_row["target_id"]),
            "name": edge_row["target_name"],
            "node_type": edge_row["target_type"],
            "description": edge_row["target_desc"],
        },
        "weight_history": [
            {
                "weight_band": r["weight_band"],
                "previous_weight_band": r["previous_weight_band"],
                "change_type": r["change_type"],
                "reasoning": r["reasoning"],
                "content_timestamp": r["content_timestamp"].isoformat() if r["content_timestamp"] else None,
            }
            for r in weight_history
        ],
        "supporting_signals": [_sig(r) for r in supporting_rows],
        "disconfirming_signals": [_sig(r) for r in disconf_rows],
        "disconfirmation_events": [
            {
                "pass_event_id": str(r["pass_event_id"]),
                "outcome": r["outcome"],
                "reasoning": r["reasoning"],
                "challenged_at": r["challenged_at"].isoformat() if r["challenged_at"] else None,
                "weight_band_at_challenge": r["weight_band_at_challenge"],
            }
            for r in disconf_events
        ],
    })
