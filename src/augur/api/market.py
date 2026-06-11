"""
Market tape API — the latest deterministic %-move per curated instrument.

GET /api/market → most-recent move for each market instrument, read from the
payload metadata the FRED and Yahoo clients write. This is the real data behind
the design's "Brent +4.2%": a live market tape, not a fabricated chip.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["market"])

_MARKET_SOURCES = ["yahoo_finance", "fred"]

# Display order by instrument class.
_CLASS_ORDER = {
    "commodity": 0,
    "currency": 1,
    "equity_index": 2,
    "volatility": 3,
    "bond_yield": 4,
}


def _pool(request: Request):
    return request.app.state.raw_pool


def _to_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


@router.get("/market")
async def market_tape(request: Request) -> JSONResponse:
    """Latest move per instrument, newest observation wins."""
    pool = _pool(request)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (metadata->>'symbol')
                   metadata->>'symbol'          AS symbol,
                   metadata->>'label'           AS label,
                   metadata->>'pct_change'      AS pct_change,
                   metadata->>'latest_value'    AS latest_value,
                   metadata->>'instrument_class' AS instrument_class,
                   source_id,
                   content_timestamp
            FROM payloads
            WHERE source_id = ANY($1)
              AND NOT rejected
              AND metadata->>'symbol' IS NOT NULL
              AND metadata->>'pct_change' IS NOT NULL
            ORDER BY metadata->>'symbol', content_timestamp DESC
            """,
            _MARKET_SOURCES,
        )

    instruments = [
        {
            "symbol": r["symbol"],
            "label": r["label"],
            "pct_change": _to_float(r["pct_change"]),
            "value": _to_float(r["latest_value"]),
            "class": r["instrument_class"] or "",
            "source": r["source_id"],
            "as_of": r["content_timestamp"].isoformat() if r["content_timestamp"] else None,
        }
        for r in rows
    ]
    instruments.sort(key=lambda x: (_CLASS_ORDER.get(x["class"], 9), x["label"] or ""))

    return JSONResponse({"instruments": instruments})
