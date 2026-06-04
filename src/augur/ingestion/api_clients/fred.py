"""
FRED (Federal Reserve Bank of St. Louis) API client.

Fetches economic time-series data for the series IDs configured in
sources.yaml.  Each observation becomes a FetchResult with a structured
JSON payload representing the data point.

FRED API docs: https://fred.stlouisfed.org/docs/api/fred/
Rate limit: 120 requests/60s without a key; key-based access is generous.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from augur.ingestion.models import FetchResult, SourceConfig

log = structlog.get_logger(__name__)

_FRED_BASE = "https://api.stlouisfed.org/fred"


class FredClient:
    """Fetch recent observations from FRED for configured series."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    def _api_key(self, source: SourceConfig) -> str | None:
        key_env = source.access_config.get("api_key_env", "FRED_API_KEY")
        return os.environ.get(key_env)

    async def fetch_source(self, source: SourceConfig) -> list[FetchResult]:
        """
        Fetch the most recent observation for each configured series.

        Returns one FetchResult per series, containing the latest value
        as a structured JSON payload.
        """
        series_ids: list[str] = source.access_config.get("series_ids", [])
        api_key = self._api_key(source)
        results: list[FetchResult] = []

        # Only fetch observations from the last 7 days to keep payloads fresh
        observation_start = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for series_id in series_ids:
                params: dict[str, Any] = {
                    "series_id": series_id,
                    "observation_start": observation_start,
                    "sort_order": "desc",
                    "limit": 5,
                    "file_type": "json",
                }
                if api_key:
                    params["api_key"] = api_key

                url = f"{_FRED_BASE}/series/observations"
                try:
                    response = await client.get(url, params=params)
                    response.raise_for_status()
                    data = response.json()
                except Exception as exc:
                    log.warning(
                        "fred.fetch_failed",
                        series_id=series_id,
                        error=str(exc),
                    )
                    continue

                observations = data.get("observations", [])
                if not observations:
                    continue

                # Most recent observation
                latest = observations[0]
                value = latest.get("value", ".")
                obs_date = latest.get("date", "")

                # Skip missing values
                if value in (".", ""):
                    continue

                content_ts = _parse_fred_date(obs_date)
                fetched_at = datetime.now(timezone.utc)

                # Build a human-readable representation for the lens to process
                series_info = _SERIES_LABELS.get(series_id, series_id)
                content = (
                    f"FRED data: {series_id} ({series_info})\n"
                    f"Latest observation: {value} on {obs_date}\n"
                    f"Previous observations: "
                    + ", ".join(
                        f"{o['date']}={o['value']}"
                        for o in observations[1:4]
                        if o.get("value") not in (".", "")
                    )
                )

                results.append(
                    FetchResult(
                        source_id=source.source_id,
                        url=f"{_FRED_BASE}/series/observations?series_id={series_id}",
                        perspective=source.perspective,
                        raw_content=content,
                        fetched_at=fetched_at,
                        content_timestamp=content_ts or fetched_at,
                        content_type="structured_feed_entry",
                        language="en",
                        metadata={
                            "series_id": series_id,
                            "series_label": series_info,
                            "latest_value": value,
                            "latest_date": obs_date,
                            "raw_observations": json.dumps(observations[:5]),
                            "source_native_id": f"fred:{series_id}:{obs_date}",
                        },
                    )
                )

        log.info(
            "fred.fetched",
            source_id=source.source_id,
            n_series=len(series_ids),
            n_results=len(results),
        )
        return results


def _parse_fred_date(date_str: str) -> datetime | None:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# Human-readable labels for FRED series IDs used in content generation
_SERIES_LABELS: dict[str, str] = {
    "DCOILWTICO": "WTI Crude Oil Price (USD/barrel)",
    "DCOILBRENTEU": "Brent Crude Oil Price (USD/barrel)",
    "GASREGCOVW": "US Regular Gasoline Price (USD/gallon)",
    "PPIACO": "US Producer Price Index — All Commodities",
    "PWHEAMTUSDM": "World Wheat Price (USD/metric ton)",
    "PCORNUSDM": "World Corn Price (USD/metric ton)",
    "PSOYBUSDM": "World Soybean Price (USD/metric ton)",
    "PNGASEUUSDM": "European Natural Gas Price (USD/MMBtu)",
    "DEXUSEU": "USD/EUR Exchange Rate",
    "DEXCHUS": "CNY/USD Exchange Rate",
    "PET.WTIPUUS.W": "US Weekly Crude Oil Production",
    "NG.N9010US2.W": "US Natural Gas Production",
}
