"""
Topics API — operator-curated named clusters of graph nodes.

GET /api/topics                → list all topics (summary)
GET /api/topics/{topic_id}    → full topic detail with nodes
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.requests import Request

from augur.presentation.topics import get_topic_detail, get_topic_list

router = APIRouter(prefix="/api/topics", tags=["topics"])


def _pool(request: Request):
    return request.app.state.raw_pool


def _parse_as_of(as_of: str | None) -> datetime | None:
    if not as_of:
        return None
    try:
        dt = datetime.fromisoformat(as_of)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid as_of timestamp")


@router.get("")
async def list_topics(pool=Depends(_pool)):
    """Return all topics with aggregated stats."""
    topics = await get_topic_list(pool)
    return {
        "topics": [
            {
                "topic_id": t.topic_id,
                "name": t.name,
                "description": t.description,
                "dimension": t.dimension,
                "node_count": t.node_count,
                "active_condition_count": t.active_condition_count,
                "state": t.state,
                "attention": t.attention,
                "created_at": t.created_at,
                "updated_at": t.updated_at,
            }
            for t in topics
        ]
    }


@router.get("/{topic_id}")
async def get_topic(
    topic_id: str,
    as_of: str | None = Query(default=None),
    pool=Depends(_pool),
):
    """Return full topic detail including member nodes."""
    dt = _parse_as_of(as_of)
    detail = await get_topic_detail(pool, topic_id, as_of=dt)
    if detail is None:
        raise HTTPException(status_code=404, detail="Topic not found")

    return {
        "topic_id": detail.topic_id,
        "name": detail.name,
        "description": detail.description,
        "dimension": detail.dimension,
        "node_count": detail.node_count,
        "active_condition_count": detail.active_condition_count,
        "state": detail.state,
        "attention": detail.attention,
        "created_at": detail.created_at,
        "updated_at": detail.updated_at,
        "nodes": [
            {
                "node_id": n.node_id,
                "name": n.name,
                "node_type": n.node_type,
                "current_state": n.current_state,
                "added_at": n.added_at,
                "notes": n.notes,
            }
            for n in detail.nodes
        ],
    }
