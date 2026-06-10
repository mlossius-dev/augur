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

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api", tags=["home"])


def _pool(request: Request):
    return request.app.state.raw_pool


def _as_of(as_of: str | None) -> datetime | None:
    if not as_of:
        return None
    try:
        return datetime.fromisoformat(as_of)
    except ValueError:
        return None


def _serialise_dimension(d: DimensionScore) -> dict[str, Any]:
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
    changes = await get_recent_changes(pool, hours=24, as_of=ts, limit=12)

    return JSONResponse({
        "as_of": (ts or datetime.utcnow()).isoformat(),
        "dimensions": [_serialise_dimension(d) for d in dimensions],
        "changes": [_serialise_change(c) for c in changes],
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
