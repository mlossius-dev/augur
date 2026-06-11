"""
FRED (Federal Reserve Bank of St. Louis) API client.

Fetches economic time-series data for the series IDs configured in
sources.yaml.  Each observation becomes a FetchResult with a structured
JSON payload representing the data point.

FRED API docs: https://fred.stlouisfed.org/docs/api/fred/
Rate limit: 120 requests/60s without a key; key-based access is generous.
"""

from __future__ import annotations

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

        # Look back far enough to catch the latest observation of *monthly*
        # series (e.g. OECD long-term yields, world crop prices) — a 7-day
        # window silently misses them. Daily series are unaffected: the client
        # always takes the most recent observation regardless of window width.
        lookback_days = int(source.access_config.get("observation_lookback_days", 45))
        observation_start = (
            datetime.now(timezone.utc) - timedelta(days=lookback_days)
        ).strftime("%Y-%m-%d")

        # Trailing window for the deterministic %-move signal. Date-adaptive:
        # for daily series this is ~N calendar days back; for monthly series it
        # naturally resolves to the prior month's observation.
        window_days = int(source.access_config.get("delta_window_days", 5))

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for series_id in series_ids:
                params: dict[str, Any] = {
                    "series_id": series_id,
                    "observation_start": observation_start,
                    "sort_order": "desc",
                    "limit": 12,
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
                move = compute_fred_move(observations, window_days)
                if move is None:
                    continue
                latest_val, prior_val, pct, latest_date, prior_date = move

                content_ts = _parse_fred_date(latest_date)
                fetched_at = datetime.now(timezone.utc)
                series_info = _SERIES_LABELS.get(series_id, series_id)

                # Deterministic market-move statement (same shape as Yahoo), so a
                # lens anchors a clean signal rather than interpreting raw numbers.
                content = _build_fred_content(
                    series_info, series_id, latest_val, prior_val, pct, latest_date, prior_date
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
                            # Keys aligned with the Yahoo client for a uniform market feed.
                            "symbol": series_id,
                            "label": series_info,
                            "series_id": series_id,
                            "latest_value": latest_val,
                            "prior_value": prior_val,
                            "pct_change": round(pct, 2),
                            "latest_date": latest_date,
                            "instrument_class": _fred_class(series_id),
                            "source_native_id": f"fred:{series_id}:{latest_date}",
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


def compute_fred_move(
    observations: list[dict[str, Any]], window_days: int
) -> tuple[float, float, float, str, str] | None:
    """
    Compute the percentage move of the latest observation versus the most recent
    observation at least ``window_days`` calendar days earlier.

    ``observations`` is FRED's response list (sorted desc, values may be "."),
    each ``{"date": "YYYY-MM-DD", "value": "..."}``. Date-adaptive: daily series
    yield a ~window-day move; monthly series resolve to the prior month.

    Returns (latest_value, prior_value, pct_change, latest_date, prior_date)
    or None if not computable.
    """
    pts: list[tuple[str, float]] = []
    for o in observations:
        v = o.get("value", ".")
        d = o.get("date", "")
        if v in (".", "", None) or not d:
            continue
        try:
            pts.append((d, float(v)))
        except (TypeError, ValueError):
            continue
    if len(pts) < 2:
        return None

    latest_date, latest_val = pts[0]
    ld = _parse_fred_date(latest_date)
    if ld is None:
        return None
    target = ld - timedelta(days=window_days)

    prior_date, prior_val = pts[-1]  # fall back to the oldest available
    for d, v in pts[1:]:
        dd = _parse_fred_date(d)
        if dd is not None and dd <= target:
            prior_date, prior_val = d, v
            break
    if prior_val == 0:
        return None

    pct = (latest_val - prior_val) / prior_val * 100.0
    return latest_val, prior_val, pct, latest_date, prior_date


def _build_fred_content(
    label: str,
    series_id: str,
    latest: float,
    prior: float,
    pct: float,
    latest_date: str,
    prior_date: str,
) -> str:
    direction = "rose" if pct > 0 else "fell" if pct < 0 else "was unchanged"
    sign = "+" if pct > 0 else ""
    return (
        f"Market move: {label} ({series_id}) {direction} {sign}{pct:.2f}% "
        f"between {prior_date} and {latest_date}, {prior:g} → {latest:g}."
    )


# Coarse instrument classification for the market tape, by FRED series id.
def _fred_class(series_id: str) -> str:
    if series_id in {"DCOILWTICO", "DCOILBRENTEU", "GOLDPMGBD228NLBM",
                     "GASREGCOVW", "PNGASEUUSDM", "PWHEAMTUSDM",
                     "PCORNUSDM", "PSOYBUSDM"}:
        return "commodity"
    if series_id.startswith("DEX") or series_id == "DTWEXBGS":
        return "currency"
    if series_id in {"SP500", "NASDAQCOM"}:
        return "equity_index"
    if series_id == "VIXCLS":
        return "volatility"
    if series_id == "DGS10" or series_id.startswith("IRLTLT01"):
        return "bond_yield"
    return ""


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
    "DTWEXBGS": "Broad Trade-Weighted US Dollar Index",
    "DEXJPUS": "JPY/USD Exchange Rate",
    "SP500": "S&P 500 Index",
    "VIXCLS": "CBOE Volatility Index (VIX)",
    "DGS10": "US 10-Year Treasury Yield (%)",
    "GOLDPMGBD228NLBM": "Gold Price, London PM Fixing (USD/oz)",
    "IRLTLT01DEM156N": "Germany 10-Year Government Bond Yield (%)",
    "IRLTLT01GBM156N": "UK 10-Year Government Bond Yield (%)",
    "IRLTLT01JPM156N": "Japan 10-Year Government Bond Yield (%)",
}
