"""
Yahoo Finance market-data client.

Fetches the curated macro instrument set (commodities, currencies, equity
indices, sovereign yields) from Yahoo's public chart endpoint and emits one
FetchResult per instrument.

Each payload is a *deterministically computed* market move — the percentage
change over a trailing window — not a bare number. This follows the
structured-data signal rule in augur-sources.md: values become signals via
deterministic computation, so a lens anchors a clean "Brent +4.2% over 5d"
statement rather than being handed a raw price to interpret.

No API key, no paid tier, no third-party library: the public chart JSON
endpoint is fetched directly over HTTP. Yahoo corroborates (and is corroborated
by) FRED on the series both providers carry.

Chart endpoint: https://query1.finance.yahoo.com/v8/finance/chart/{symbol}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from augur.ingestion.models import FetchResult, SourceConfig

log = structlog.get_logger(__name__)

_CHART_BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
# Yahoo rate-limits requests without a browser-like User-Agent.
_USER_AGENT = "Mozilla/5.0 (compatible; AugurResearchBot/0.1)"


class YahooFinanceClient:
    """Fetch curated market instruments from Yahoo's public chart endpoint."""

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    async def fetch_source(self, source: SourceConfig) -> list[FetchResult]:
        """
        Fetch each configured instrument and emit a deterministic market-move
        FetchResult (percentage change over the configured trailing window).
        """
        instruments: list[dict[str, Any]] = source.access_config.get("instruments", [])
        window = int(source.access_config.get("delta_window_days", 5))
        results: list[FetchResult] = []

        async with httpx.AsyncClient(
            timeout=self._timeout, headers={"User-Agent": _USER_AGENT}
        ) as client:
            for inst in instruments:
                if not inst.get("symbol"):
                    continue
                fr = await self._fetch_one(client, source, inst, window)
                if fr is not None:
                    results.append(fr)

        log.info(
            "yahoo.fetched",
            source_id=source.source_id,
            n_instruments=len(instruments),
            n_results=len(results),
        )
        return results

    async def _fetch_one(
        self,
        client: httpx.AsyncClient,
        source: SourceConfig,
        inst: dict[str, Any],
        window: int,
    ) -> FetchResult | None:
        symbol = inst["symbol"]
        label = inst.get("label", symbol)

        url = f"{_CHART_BASE}/{symbol}"
        try:
            resp = await client.get(url, params={"range": "1mo", "interval": "1d"})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("yahoo.fetch_failed", symbol=symbol, error=str(exc))
            return None

        parsed = _extract_closes(data)
        if parsed is None:
            log.warning("yahoo.no_closes", symbol=symbol)
            return None
        closes, last_ts = parsed
        if len(closes) < 2:
            return None

        move = compute_move(closes, window)
        if move is None:
            return None
        latest, prior, pct, used_window = move

        content = _build_content(label, symbol, latest, prior, pct, used_window, inst.get("unit", ""))
        fetched_at = datetime.now(timezone.utc)
        content_ts = (
            datetime.fromtimestamp(last_ts, tz=timezone.utc) if last_ts else fetched_at
        )

        return FetchResult(
            source_id=source.source_id,
            url=url,
            perspective=source.perspective,
            raw_content=content,
            fetched_at=fetched_at,
            content_timestamp=content_ts,
            content_type="structured_feed_entry",
            language="en",
            metadata={
                "symbol": symbol,
                "label": label,
                "latest_value": round(latest, 6),
                "prior_value": round(prior, 6),
                "pct_change": round(pct, 2),
                "window_trading_days": used_window,
                "instrument_class": inst.get("class", ""),
                "dimension_hint": inst.get("dimension", ""),
                "source_native_id": f"yahoo:{symbol}:{content_ts.date().isoformat()}",
            },
        )


# ── Pure helpers (unit-testable without network) ──────────────────────────────


def _extract_closes(data: dict[str, Any]) -> tuple[list[float], int | None] | None:
    """
    Pull the daily close series and latest timestamp out of a Yahoo chart
    response. Returns (closes, last_timestamp) or None if the shape is wrong.
    """
    try:
        result = data["chart"]["result"][0]
    except (KeyError, IndexError, TypeError):
        return None

    timestamps = result.get("timestamp") or []
    try:
        closes_raw = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(closes_raw, list):
        return None

    closes = [float(c) for c in closes_raw if c is not None]
    if not closes:
        return None

    last_ts = int(timestamps[-1]) if timestamps else None
    return closes, last_ts


def compute_move(
    closes: list[float], window: int
) -> tuple[float, float, float, int] | None:
    """
    Compute the percentage move of the latest close versus the close `window`
    trading days earlier. The window is clamped to the available history.

    Returns (latest, prior, pct_change, used_window) or None if not computable.
    """
    if len(closes) < 2 or window < 1:
        return None
    latest = closes[-1]
    idx = max(0, len(closes) - 1 - window)
    prior = closes[idx]
    used_window = (len(closes) - 1) - idx
    if prior == 0:
        return None
    pct = (latest - prior) / prior * 100.0
    return latest, prior, pct, used_window


def _build_content(
    label: str,
    symbol: str,
    latest: float,
    prior: float,
    pct: float,
    window: int,
    unit: str,
) -> str:
    """Render a clean, pre-computed market-move statement for the lens."""
    direction = "rose" if pct > 0 else "fell" if pct < 0 else "was flat"
    sign = "+" if pct > 0 else ""
    u = f" {unit}" if unit else ""
    return (
        f"Market move: {label} ({symbol}) {direction} {sign}{pct:.2f}% "
        f"over the last {window} trading days, "
        f"{_fmt(prior)}{u} → {_fmt(latest)}{u}."
    )


def _fmt(v: float) -> str:
    """Compact numeric formatting: trims trailing zeros, caps precision."""
    return f"{v:,.4f}".rstrip("0").rstrip(".")
