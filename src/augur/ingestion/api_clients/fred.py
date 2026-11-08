"""
FRED (Federal Reserve Bank of St. Louis) client.

Reads series through FRED's public CSV download, which needs no key
(docs/augur-sources.md, FRED; the keyed API returned nothing while no key
was set). Two modes, by source:

  - market moves (`fred`): each series' latest move over a trailing window,
    as a deterministic "Market move: …" statement;
  - radar (`fred_radar`, access_config.radar): each series judged for how
    unusual its level and 60-day trend are against three years, a payload
    only when it is (docs/augur-signal-pipeline.md, "The radar").
"""

from __future__ import annotations

import csv
import io
import statistics
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from augur.ingestion.models import FetchResult, SourceConfig

log = structlog.get_logger(__name__)

_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
_FRED_SERIES_PAGE = "https://fred.stlouisfed.org/series"


def parse_fred_csv(text: str) -> list[dict[str, str]]:
    """FRED's CSV (header row, then date,value) as API-style observations, newest first."""
    rows = list(csv.reader(io.StringIO(text)))[1:]
    return [{"date": r[0], "value": r[1]} for r in reversed(rows) if len(r) >= 2]


async def fetch_observations(client: httpx.AsyncClient, series_id: str, start: str) -> list[dict[str, str]]:
    response = await client.get(_FRED_CSV, params={"id": series_id, "cosd": start})
    response.raise_for_status()
    return parse_fred_csv(response.text)


class FredClient:
    """Fetch recent observations from FRED for configured series."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def fetch_source(self, source: SourceConfig) -> list[FetchResult]:
        """
        Fetch the most recent observation for each configured series.

        Returns one FetchResult per series, containing the latest value
        as a structured JSON payload.
        """
        if source.access_config.get("radar"):
            return await self._radar(source)
        series_ids: list[str] = source.access_config.get("series_ids", [])
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

        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            for series_id in series_ids:
                try:
                    observations = (await fetch_observations(client, series_id, observation_start))[:12]
                except Exception as exc:
                    log.warning(
                        "fred.fetch_failed",
                        series_id=series_id,
                        error=str(exc),
                    )
                    continue

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
                        url=f"{_FRED_SERIES_PAGE}/{series_id}",
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


    async def _radar(self, source: SourceConfig) -> list[FetchResult]:
        """One payload per series whose level or 60-day trend is unusual."""
        series: dict[str, dict[str, Any]] = source.access_config.get("series", {})
        start = (datetime.now(timezone.utc) - timedelta(days=RADAR_HISTORY_DAYS)).strftime("%Y-%m-%d")
        results: list[FetchResult] = []
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            for series_id, spec in series.items():
                try:
                    observations = await fetch_observations(client, series_id, start)
                except Exception as exc:
                    log.warning("fred.radar_fetch_failed", series_id=series_id, error=str(exc))
                    continue
                reading = assess_radar(observations, direction=int(spec.get("direction", 1)))
                if reading is None or reading.flag is None:
                    continue
                label = spec.get("label", series_id)
                unit = spec.get("unit", "")
                fetched_at = datetime.now(timezone.utc)
                content_ts = _parse_fred_date(reading.latest_date) or fetched_at
                results.append(FetchResult(
                    source_id=source.source_id,
                    url=f"{_FRED_SERIES_PAGE}/{series_id}",
                    perspective=source.perspective,
                    raw_content=radar_statement(label, unit, reading),
                    fetched_at=fetched_at,
                    content_timestamp=content_ts,
                    content_type="structured_feed_entry",
                    language="en",
                    metadata={
                        "kind": "radar",
                        "series_id": series_id,
                        "label": label,
                        "flag": reading.flag,
                        "latest_value": reading.latest,
                        "change_60d": round(reading.change, 4),
                        "z_60d": round(reading.z, 2),
                        "percentile_3y": round(reading.percentile),
                        "direction": int(spec.get("direction", 1)),
                        "lens": spec.get("lens", "financial"),
                        "dimension": spec.get("dimension", "economic_stability"),
                        "latest_date": reading.latest_date,
                        # One payload per series, day and flag
                        "source_native_id": f"fred_radar:{series_id}:{reading.latest_date}:{reading.flag}",
                    },
                ))
        log.info("fred.radar", n_series=len(series), n_flagged=len(results))
        return results


# ── Radar (docs/augur-signal-pipeline.md, "The radar") ─────────────────────────

RADAR_HISTORY_DAYS = 3 * 365
RADAR_TREND_DAYS = 60
RADAR_STRETCHED_Z = 1.5
RADAR_WATCH_Z = 1.0
RADAR_EXTREME_PERCENTILE = 95.0


@dataclass(frozen=True)
class RadarReading:
    latest: float
    latest_date: str
    change: float        # over RADAR_TREND_DAYS
    z: float             # that change against all 60-day changes in the history
    percentile: float    # the latest level's percentile in the history
    risk: float          # z signed so that positive is the risky direction
    flag: str | None     # "stretched" | "watch" | None


def assess_radar(observations: list[dict[str, str]], *, direction: int = 1) -> RadarReading | None:
    """
    Judge a series' latest level and 60-day change against its history.

    `observations` newest first (FRED API shape); `direction` +1 when rising is
    the risky way (spreads, VIX), -1 when falling is (yield curves). None when
    the history is too short to judge.
    """
    pts: list[tuple[datetime, float, str]] = []
    for o in reversed(observations):
        v, d = o.get("value", "."), o.get("date", "")
        dt = _parse_fred_date(d) if d else None
        if v in (".", "", None) or dt is None:
            continue
        try:
            pts.append((dt, float(v), d))
        except (TypeError, ValueError):
            continue
    if len(pts) < 30:
        return None
    dates = [p[0] for p in pts]
    values = [p[1] for p in pts]
    # The observation about RADAR_TREND_DAYS before each one, whatever the frequency
    changes: list[float] = []
    j = 0
    for i, dt in enumerate(dates):
        while j < i and dates[j + 1] <= dt - timedelta(days=RADAR_TREND_DAYS):
            j += 1
        if dates[j] <= dt - timedelta(days=RADAR_TREND_DAYS):
            changes.append(values[i] - values[j])
    if len(changes) < 10:
        return None
    sd = statistics.pstdev(changes) or 1e-9
    z = (changes[-1] - statistics.mean(changes)) / sd
    latest = values[-1]
    percentile = 100.0 * sum(v <= latest for v in values) / len(values)
    risk = direction * z
    extreme = percentile >= RADAR_EXTREME_PERCENTILE if direction > 0 else percentile <= 100 - RADAR_EXTREME_PERCENTILE
    flag = ("stretched" if risk >= RADAR_STRETCHED_Z or extreme
            else "watch" if risk >= RADAR_WATCH_Z else None)
    return RadarReading(latest, pts[-1][2], changes[-1], z, percentile, risk, flag)


def radar_statement(label: str, unit: str, r: RadarReading) -> str:
    """'Building risk: US high-yield spread at 3.48 %, +81 bp over 60 days, …'"""
    if unit == "percent":
        level, change = f"{r.latest:.2f} %", f"{r.change * 100:+.0f} bp"
    else:
        level, change = f"{r.latest:,.2f}", f"{r.change:+,.2f}"
    lean = "above" if r.z > 0 else "below"
    head = "Building risk" if r.flag == "stretched" else "Risk on watch"
    return (f"{head}: {label} at {level}, {change} over {RADAR_TREND_DAYS} days, "
            f"{abs(r.z):.1f} standard deviations {lean} its usual {RADAR_TREND_DAYS}-day change; "
            f"{_ordinal(round(r.percentile))} percentile of three years.")


def _ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


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
