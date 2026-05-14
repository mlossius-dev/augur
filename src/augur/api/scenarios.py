"""
Scenarios API — LLM-generated near-term forecasts from graph state.

GET /api/scenarios              → all current scenarios (latest per dimension)
GET /api/scenarios/{scenario_id} → single scenario
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.requests import Request

from augur.projection.store import get_scenarios

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


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


def _serialise(s) -> dict:
    return {
        "scenario_id": s.scenario_id,
        "dimension": s.dimension,
        "title": s.title,
        "summary": s.summary,
        "probability_band": s.probability_band,
        "time_horizon": s.time_horizon,
        "key_condition_ids": s.key_condition_ids,
        "supporting_edge_ids": s.supporting_edge_ids,
        "contradicting_edge_ids": s.contradicting_edge_ids,
        "generated_at": s.generated_at,
        "as_of": s.as_of,
        "model_used": s.model_used,
    }


@router.get("")
async def list_scenarios(
    dimension: str | None = Query(default=None, description="Filter by dimension"),
    as_of: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    pool=Depends(_pool),
):
    """Return current (non-deprecated) scenarios, optionally filtered by dimension."""
    dt = _parse_as_of(as_of)
    scenarios = await get_scenarios(pool, dimension=dimension, as_of=dt, limit=limit)
    return {
        "scenarios": [_serialise(s) for s in scenarios],
        "count": len(scenarios),
        "dimension": dimension,
        "as_of": as_of,
    }


@router.get("/{scenario_id}")
async def get_scenario(
    scenario_id: str,
    pool=Depends(_pool),
):
    """Return a single scenario by ID."""
    scenarios = await get_scenarios(pool, limit=1000, include_deprecated=True)
    match = next((s for s in scenarios if s.scenario_id == scenario_id), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return _serialise(match)
