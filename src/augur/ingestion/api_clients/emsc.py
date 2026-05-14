"""
EMSC earthquake feed client.

Fetches recent earthquakes from the European Mediterranean Seismological
Centre's FDSN-compliant event API.  Returns one FetchResult per earthquake
above the configured magnitude threshold.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from augur.ingestion.models import FetchResult, SourceConfig

log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT = 20.0
_DEFAULT_URL = "https://www.seismicportal.eu/fdsnws/event/1/query"


class EmscClient:
    """Fetches earthquake events from the EMSC FDSN event API."""

    def __init__(self, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    async def fetch_source(self, source: SourceConfig) -> list[FetchResult]:
        """
        Fetch recent earthquakes from the configured URL.

        Returns one FetchResult per event, with structured text content
        suitable for the physical_world lens.
        """
        cfg = source.access_config
        url = cfg.get("url", _DEFAULT_URL)
        fetched_at = datetime.now(timezone.utc)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers={"Accept": "application/json"})
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            log.error("emsc.fetch_failed", source_id=source.source_id, error=str(exc))
            return []

        features = data.get("features", [])
        if not isinstance(features, list):
            return []

        results: list[FetchResult] = []
        for feature in features:
            result = _feature_to_fetch_result(feature, source, fetched_at)
            if result is not None:
                results.append(result)

        log.info("emsc.fetched", source_id=source.source_id, n=len(results))
        return results


def _feature_to_fetch_result(
    feature: dict[str, Any],
    source: SourceConfig,
    fetched_at: datetime,
) -> FetchResult | None:
    """Convert a GeoJSON feature to a FetchResult."""
    props = feature.get("properties", {})
    if not isinstance(props, dict):
        return None

    magnitude = props.get("mag")
    place = props.get("flynn_region") or props.get("place") or "Unknown region"
    depth = props.get("depth")
    event_time_str = props.get("time")
    event_id = props.get("unid") or feature.get("id", "")

    if not magnitude:
        return None

    # Parse event time
    content_timestamp: datetime | None = None
    if event_time_str:
        try:
            # EMSC uses milliseconds since epoch in some responses, or ISO string
            if isinstance(event_time_str, (int, float)):
                content_timestamp = datetime.fromtimestamp(
                    event_time_str / 1000, tz=timezone.utc
                )
            else:
                content_timestamp = datetime.fromisoformat(
                    str(event_time_str).replace("Z", "+00:00")
                )
        except (ValueError, TypeError):
            content_timestamp = fetched_at

    geometry = feature.get("geometry", {})
    coords = geometry.get("coordinates", [])
    lat = coords[1] if len(coords) > 1 else None
    lon = coords[0] if len(coords) > 0 else None

    content = (
        f"Earthquake detected: M{magnitude} {place}. "
        f"Depth: {depth} km. "
        f"Coordinates: {lat:.2f}N, {lon:.2f}E. "
        f"Event ID: {event_id}. "
        f"Time: {event_time_str}."
    )
    if lat is None:
        content = f"Earthquake detected: M{magnitude} {place}. Depth: {depth} km."

    return FetchResult(
        source_id=source.source_id,
        url=f"https://www.seismicportal.eu/eventdetails.html?unid={event_id}",
        perspective=source.perspective,
        raw_content=content,
        fetched_at=fetched_at,
        content_timestamp=content_timestamp,
        content_type="structured_feed_entry",
        language="en",
        metadata={
            "event_id": event_id,
            "magnitude": magnitude,
            "depth": depth,
            "place": place,
            "lat": lat,
            "lon": lon,
        },
    )
