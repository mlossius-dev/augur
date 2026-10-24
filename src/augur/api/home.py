"""
Home view API endpoints.

GET /api/home              — five-dimension scores + 24h changes
GET /api/home/changes      — recent changes (parametric window)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from augur.presentation.changes import ChangeRecord, get_recent_changes
from augur.presentation.dimensions import DimensionScore, compute_dimension_scores
from augur.presentation.notes import get_dimension_notes

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api", tags=["home"])


_HOME_CHANGES_LIMIT = 200
# How far back a topic's latest change is looked for
_TOPIC_LATEST_HOURS = 24 * 30
_TOPIC_LATEST_LIMIT = 3000

def _pool(request: Request):
    return request.app.state.raw_pool


def _as_of(as_of: str | None) -> datetime | None:
    if not as_of:
        return None
    try:
        return datetime.fromisoformat(as_of)
    except ValueError:
        return None


def _serialise_dimension(d: DimensionScore, note: str = "") -> dict[str, Any]:
    return {
        "dimension": d.dimension,
        "label": d.label,
        "state": d.state,
        "direction": d.direction,
        "active_conditions": d.active_conditions,
        "total_conditions": d.total_conditions,
        "strong_edge_count": d.strong_edge_count,
        "weak_edge_count": d.weak_edge_count,
        "rate": d.rate,
        "rate_label": d.rate_label,
        "acceleration": d.acceleration,
        "accel_label": d.accel_label,
        "note": note,
        "sparkline": [
            {
                "week_start": sp.week_start,
                "active_count": sp.active_count,
                "total_count": sp.total_count,
            }
            for sp in d.sparkline
        ],
    }


def _serialise_change(c: ChangeRecord) -> dict[str, Any]:
    return {
        "change_id": c.change_id,
        "change_type": c.change_type,
        "summary": c.summary,
        "dimension": c.dimension,
        "dimension_label": c.dimension_label,
        "occurred_at": c.occurred_at,
        "target_id": c.target_id,
        "target_type": c.target_type,
        "target_name": c.target_name,
        "weight_before": c.weight_before,
        "weight_after": c.weight_after,
        "impact_rank": c.impact_rank,
        "downstream_edge_count": c.downstream_edge_count,
        "topic_ids": c.topic_ids,
        "sources": c.sources,
    }


@router.get("/home")
async def home_view(
    request: Request,
    as_of: str | None = Query(None, description="ISO datetime for time scrubber"),
) -> JSONResponse:
    """
    Home view data: five dimension scores plus 24-hour change log.

    The as_of parameter powers the time scrubber — pass an ISO timestamp
    to see what Augur would have shown at that moment.
    """
    pool = _pool(request)
    ts = _as_of(as_of)

    dimensions = await compute_dimension_scores(pool, as_of=ts)
    # The overview, cards and ledgers count these, so fetch the whole window
    # (60 changes on a busy day so far), not a display-sized handful.
    changes = await get_recent_changes(pool, hours=24, as_of=ts, limit=_HOME_CHANGES_LIMIT)

    # Each topic's most recent change, however old (docs/augur-presentation.md,
    # topics): the threads never show a topic as empty because the day was quiet
    older = await get_recent_changes(pool, hours=_TOPIC_LATEST_HOURS, as_of=ts,
                                     limit=_TOPIC_LATEST_LIMIT, rows_per_kind=_TOPIC_LATEST_LIMIT)
    topic_latest: dict[str, ChangeRecord] = {}
    for c in older:
        for tid in c.topic_ids:
            if tid not in topic_latest or c.occurred_at > topic_latest[tid].occurred_at:
                topic_latest[tid] = c

    # Editorial notes reflect the *current* 24h; only attach them to the live view.
    notes = await get_dimension_notes(pool) if ts is None else {}

    return JSONResponse({
        "as_of": (ts or datetime.utcnow()).isoformat(),
        "dimensions": [_serialise_dimension(d, notes.get(d.dimension, "")) for d in dimensions],
        "changes": [_serialise_change(c) for c in changes],
        # True when the window held more changes than were returned
        "changes_truncated": len(changes) >= _HOME_CHANGES_LIMIT,
        "topic_latest": {tid: _serialise_change(c) for tid, c in topic_latest.items()},
    })


@router.get("/home/changes")
async def home_changes(
    request: Request,
    hours: int = Query(24, ge=1, le=720, description="Lookback window in hours"),
    limit: int = Query(20, ge=1, le=100),
    as_of: str | None = Query(None),
) -> JSONResponse:
    """
    Parametric change log — same data as /api/home but with configurable window.

    Used by the time scrubber for extended history.
    """
    pool = _pool(request)
    ts = _as_of(as_of)
    changes = await get_recent_changes(pool, hours=hours, as_of=ts, limit=limit)
    return JSONResponse([_serialise_change(c) for c in changes])
