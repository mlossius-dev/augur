"""
Geographic scope API.

GET /api/geo/scope?lat=&lon=&as_of=
  → resolve lat/lon to a region, return filtered dimension scores and changes
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.requests import Request

from augur.presentation.geo import get_regional_scope

router = APIRouter(prefix="/api/geo", tags=["geo"])


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


@router.get("/scope")
async def geo_scope(
    lat: float = Query(..., ge=-90, le=90, description="Latitude"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude"),
    as_of: str | None = Query(default=None),
    pool=Depends(_pool),
):
    """
    Return dimension scores and recent changes scoped to the geographic region
    that contains the provided lat/lon.
    """
    dt = _parse_as_of(as_of)
    scope = await get_regional_scope(pool, lat, lon, as_of=dt)
    if scope is None:
        raise HTTPException(status_code=404, detail="No region found for coordinates")

    return {
        "region": {
            "region_id": scope.region.region_id,
            "display_name": scope.region.display_name,
            "perspectives": scope.region.perspectives,
            "entity_keywords": scope.region.entity_keywords,
        },
        "as_of": scope.as_of,
        "dimensions": [
            {
                "dimension": d.dimension,
                "label": d.label,
                "state": d.state,
                "direction": d.direction,
                "active_conditions": d.active_conditions,
                "total_conditions": d.total_conditions,
                "sparkline": [
                    {
                        "week_start": p.week_start,
                        "active_count": p.active_count,
                        "total_count": p.total_count,
                    }
                    for p in d.sparkline
                ],
            }
            for d in scope.dimensions
        ],
        "changes": [
            {
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
            }
            for c in scope.changes
        ],
    }
