"""
Geographic scope API.

GET /api/geo/scope?lat=&lon=&as_of=
  → resolve lat/lon to a region, return filtered dimension scores and changes
GET /api/geo/auto?as_of=
  → fallback when the browser denies geolocation: resolve the caller's IP to a
    coarse lat/lon, then the same regional scope.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.requests import Request

from augur.presentation.geo import get_regional_scope

log = structlog.get_logger(__name__)
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


def _serialise_scope(
    scope: Any,
    *,
    approximate: bool = False,
    ip_location: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "region": {
            "region_id": scope.region.region_id,
            "display_name": scope.region.display_name,
            "perspectives": scope.region.perspectives,
            "entity_keywords": scope.region.entity_keywords,
        },
        "as_of": scope.as_of,
        "approximate": approximate,
        "ip_location": ip_location,
        "dimensions": [
            {
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
                "impact_rank": c.impact_rank,
                "downstream_edge_count": c.downstream_edge_count,
                "topic_ids": c.topic_ids,
            }
            for c in scope.changes
        ],
    }


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
    return _serialise_scope(scope)


def _client_ip(request: Request) -> str | None:
    """Best-effort real client IP, honouring a proxy's X-Forwarded-For."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


def _is_public_ip(ip: str | None) -> bool:
    if not ip:
        return False
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


@router.get("/auto")
async def geo_auto(
    request: Request,
    as_of: str | None = Query(default=None),
    pool=Depends(_pool),
):
    """
    Fallback scoping when the browser denies geolocation: resolve the caller's
    IP to a coarse lat/lon via a free service, then return the regional scope.

    A private/proxy/localhost caller IP is left blank so the service geolocates
    the server's egress IP instead — coarse, but better than nothing for a
    single-operator tool. The response is flagged ``approximate``.
    """
    ip = _client_ip(request)
    lookup = ip if _is_public_ip(ip) else ""

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"http://ip-api.com/json/{lookup}",
                params={"fields": "status,message,lat,lon,city,regionName,country"},
            )
            resp.raise_for_status()
            geo = resp.json()
    except Exception as exc:
        log.warning("geo.ip_lookup_failed", error=str(exc))
        raise HTTPException(status_code=502, detail="IP geolocation unavailable")

    if geo.get("status") != "success" or geo.get("lat") is None:
        raise HTTPException(status_code=404, detail="Could not resolve location from IP")

    lat, lon = float(geo["lat"]), float(geo["lon"])
    dt = _parse_as_of(as_of)
    scope = await get_regional_scope(pool, lat, lon, as_of=dt)
    if scope is None:
        raise HTTPException(status_code=404, detail="No region found for coordinates")

    ip_location = {
        "lat": lat,
        "lon": lon,
        "city": geo.get("city"),
        "region": geo.get("regionName"),
        "country": geo.get("country"),
    }
    return _serialise_scope(scope, approximate=True, ip_location=ip_location)
