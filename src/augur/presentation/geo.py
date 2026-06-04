"""
Geographic scoping: map a lat/lon to a perspective pool, then return
dimension scores and recent changes filtered to that pool.

The region_scope_definitions table is seeded by migration 005; operators
can add custom regions via SQL or the CLI.  This module reads from that
table rather than hard-coding, so the definitions stay in the DB as the
source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg
import structlog

log = structlog.get_logger(__name__)


@dataclass
class RegionScope:
    """The resolved geographic scope for a lat/lon query."""
    region_id: str
    display_name: str
    perspectives: list[str]
    entity_keywords: list[str]
    lat: float | None = None
    lon: float | None = None


@dataclass
class GeoScopeResponse:
    """Full API response for /api/geo/scope."""
    region: RegionScope
    dimensions: list[Any]  # list[DimensionScore] — typed as Any to avoid circular
    changes: list[Any]     # list[ChangeRecord]
    as_of: str | None


async def load_region_definitions(pool: asyncpg.Pool) -> list[dict]:
    """Load all region definitions from the database."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT region_id, display_name, perspectives, entity_keywords,
                   lat_min, lat_max, lon_min, lon_max
            FROM region_scope_definitions
            ORDER BY region_id
            """
        )
    return [dict(r) for r in rows]


def infer_region(
    lat: float,
    lon: float,
    region_definitions: list[dict],
) -> dict | None:
    """
    Return the best-matching region definition for a lat/lon pair.

    Preference order:
      1. Most specific bounding box match (smallest area first)
      2. 'global' as catch-all if nothing else matches
    """
    candidates = []
    for r in region_definitions:
        if r["region_id"] == "global":
            continue
        lat_min = r.get("lat_min")
        lat_max = r.get("lat_max")
        lon_min = r.get("lon_min")
        lon_max = r.get("lon_max")
        if None in (lat_min, lat_max, lon_min, lon_max):
            continue
        if lat_min <= lat <= lat_max and lon_min <= lon <= lon_max:
            area = (lat_max - lat_min) * (lon_max - lon_min)
            candidates.append((area, r))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    # Fall back to global
    for r in region_definitions:
        if r["region_id"] == "global":
            return r

    return None


async def get_regional_scope(
    pool: asyncpg.Pool,
    lat: float,
    lon: float,
    as_of: datetime | None = None,
) -> GeoScopeResponse | None:
    """
    Resolve a lat/lon to a region, then return dimension scores and changes
    filtered to the perspectives of that region.
    """
    from augur.presentation.dimensions import compute_dimension_scores
    from augur.presentation.changes import get_recent_changes

    region_defs = await load_region_definitions(pool)
    matched = infer_region(lat, lon, region_defs)
    if not matched:
        return None

    region = RegionScope(
        region_id=matched["region_id"],
        display_name=matched["display_name"],
        perspectives=list(matched["perspectives"] or []),
        entity_keywords=list(matched["entity_keywords"] or []),
        lat=lat,
        lon=lon,
    )

    # Compute scores — if we have entity keywords, filter nodes by those keywords.
    # Without a real perspective column on nodes we use the keyword list to
    # pre-filter; the perspective filtering is primarily for future phases
    # where nodes will carry perspective tags.
    dimensions = await compute_dimension_scores(pool, as_of=as_of)
    changes = await get_recent_changes(pool, as_of=as_of)

    # Filter changes to those matching any entity keyword for this region
    if region.entity_keywords:
        kws = [k.lower() for k in region.entity_keywords]
        changes = [
            c for c in changes
            if any(kw in c.target_name.lower() or kw in c.summary.lower() for kw in kws)
        ]

    as_of_str = as_of.isoformat() if as_of else None

    return GeoScopeResponse(
        region=region,
        dimensions=dimensions,
        changes=changes,
        as_of=as_of_str,
    )
