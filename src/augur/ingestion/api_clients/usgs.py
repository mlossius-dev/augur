"""
USGS Earthquake Hazards Program API client.

Fetches seismic events at or above the configured minimum magnitude
for a recent time window.  Each event becomes a FetchResult containing
a human-readable description plus structured metadata.

API docs: https://earthquake.usgs.gov/fdsnws/event/1/
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from augur.ingestion.models import FetchResult, SourceConfig

log = structlog.get_logger(__name__)

_USGS_BASE = "https://earthquake.usgs.gov"


class UsgsClient:
    """Fetch recent earthquake events from USGS."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def fetch_source(self, source: SourceConfig) -> list[FetchResult]:
        """
        Fetch earthquake events for the last `window_hours` hours
        at or above `minmagnitude`.
        """
        cfg = source.access_config
        endpoint = cfg.get("endpoint", "/fdsnws/event/1/query")
        defaults = cfg.get("default_params", {})
        window_hours: int = int(cfg.get("window_hours", 24))

        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(hours=window_hours)

        params: dict[str, Any] = {
            **defaults,
            "starttime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "endtime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
        }

        url = f"{_USGS_BASE}{endpoint}"
        fetched_at = datetime.now(timezone.utc)

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            log.warning("usgs.fetch_failed", error=str(exc))
            return []

        features = data.get("features", [])
        results: list[FetchResult] = []

        for feature in features:
            props = feature.get("properties", {})
            geometry = feature.get("geometry", {})
            coords = geometry.get("coordinates", [None, None, None])

            mag = props.get("mag")
            place = props.get("place", "unknown location")
            event_time_ms = props.get("time")
            event_id = props.get("ids", feature.get("id", ""))
            mag_type = props.get("magType", "M")
            depth_km = coords[2] if len(coords) > 2 else None

            if mag is None:
                continue

            content_ts: datetime | None = None
            if event_time_ms:
                try:
                    content_ts = datetime.fromtimestamp(event_time_ms / 1000, tz=timezone.utc)
                except Exception:
                    pass

            content = (
                f"USGS Earthquake: {mag_type}{mag:.1f} — {place}\n"
                f"Time: {content_ts.isoformat() if content_ts else 'unknown'}\n"
                f"Depth: {depth_km:.1f} km\n"
                f"Coordinates: {coords[1]:.3f}°N, {coords[0]:.3f}°E\n"
            ) if depth_km is not None else (
                f"USGS Earthquake: {mag_type}{mag:.1f} — {place}\n"
                f"Time: {content_ts.isoformat() if content_ts else 'unknown'}\n"
            )

            results.append(
                FetchResult(
                    source_id=source.source_id,
                    url=props.get("url", url),
                    perspective=source.perspective,
                    raw_content=content,
                    fetched_at=fetched_at,
                    content_timestamp=content_ts or fetched_at,
                    content_type="structured_feed_entry",
                    language="en",
                    metadata={
                        "event_id": event_id,
                        "magnitude": mag,
                        "mag_type": mag_type,
                        "place": place,
                        "latitude": coords[1] if len(coords) > 1 else None,
                        "longitude": coords[0] if len(coords) > 0 else None,
                        "depth_km": depth_km,
                        "source_native_id": f"usgs:{event_id}",
                        "raw_properties": json.dumps(props),
                    },
                )
            )

        log.info(
            "usgs.fetched",
            source_id=source.source_id,
            n_events=len(results),
            min_magnitude=defaults.get("minmagnitude"),
        )
        return results
