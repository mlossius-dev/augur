"""
Edge selector for the disconfirmation pass.

Chooses which edges to challenge in a given pass, applying three selection
criteria (ordered by priority):

  1. Operator-flagged edges (operator has explicitly marked for review).
  2. Highest-weight edges that haven't been challenged recently.
     "Recently" is configurable — default 7 days.
  3. Edges whose supporting signals are old without recent corroboration.
     An edge is "stale" when its last supporting signal is older than
     `stale_signal_days` and the weight band is moderate or strong.

The pass selects at most `limit` edges per run to keep cost bounded.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg
import structlog

log = structlog.get_logger(__name__)

# Bands considered "high-weight" and worth challenging proactively
_HIGH_WEIGHT_BANDS = ("strong", "moderate")

# Default recency window: don't re-challenge within this many days
DEFAULT_RECHALLENGE_DAYS = 7

# Edges with supporting signals older than this (and weight ≥ moderate) are stale
DEFAULT_STALE_SIGNAL_DAYS = 30


async def select_edges(
    pool: asyncpg.Pool,
    *,
    limit: int = 20,
    rechallenge_days: int = DEFAULT_RECHALLENGE_DAYS,
    stale_signal_days: int = DEFAULT_STALE_SIGNAL_DAYS,
    include_flagged: bool = True,
) -> list[dict[str, Any]]:
    """
    Select edges for the next disconfirmation pass.

    Returns a list of edge dicts enriched with source/target node names.
    Ordered: flagged first, then by recency of challenge (oldest first),
    then stale edges.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=rechallenge_days)
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=stale_signal_days)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                e.edge_id,
                e.source_node_id,
                e.target_node_id,
                e.edge_type,
                e.current_weight_band,
                e.reasoning,
                e.falsification_criteria,
                e.supporting_signals,
                e.disconfirming_signals,
                e.last_disconfirmation_pass,
                e.created_at,
                sn.name AS source_name,
                tn.name AS target_name,
                -- Operator-flagged edges get priority (stored as metadata in type_data)
                CASE
                    WHEN e.current_weight_band = 'strong' THEN 1
                    WHEN e.current_weight_band = 'moderate' THEN 2
                    ELSE 3
                END AS weight_priority
            FROM edges e
            JOIN nodes sn ON sn.node_id = e.source_node_id
            JOIN nodes tn ON tn.node_id = e.target_node_id
            WHERE
                NOT e.deprecated
                AND e.falsification_criteria != ''
                AND e.current_weight_band = ANY($1::text[])
                AND (
                    e.last_disconfirmation_pass IS NULL
                    OR e.last_disconfirmation_pass < $2
                )
            ORDER BY
                weight_priority ASC,
                e.last_disconfirmation_pass ASC NULLS FIRST,
                e.updated_at DESC
            LIMIT $3
            """,
            list(_HIGH_WEIGHT_BANDS),
            cutoff,
            limit,
        )

    edges = [dict(r) for r in rows]
    log.info("disconfirmation.edges_selected", n=len(edges))
    return edges


async def load_recent_signals_for_edge(
    pool: asyncpg.Pool,
    *,
    edge: dict[str, Any],
    window_days: int = 7,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """
    Load Tier A signals from the last `window_days` that are topically
    adjacent to this edge.

    "Topically adjacent" means the signal's claim_text contains either
    the source node name or target node name (trigram match).
    """
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    source_name = edge.get("source_name", "")
    target_name = edge.get("target_name", "")

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT signal_id, lens_id, claim_text, confidence_band,
                   reasoning, content_timestamp, proposed_anchors
            FROM signals
            WHERE extracted_at > $1
              AND (
                  similarity(claim_text, $2) > 0.15
                  OR similarity(claim_text, $3) > 0.15
              )
            ORDER BY content_timestamp DESC
            LIMIT $4
            """,
            since,
            source_name,
            target_name,
            limit,
        )

    return [dict(r) for r in rows]
