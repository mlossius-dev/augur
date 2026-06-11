"""
Notable events API — operator-curated timeline markers for the scrubber.

GET /api/events → events ordered by occurred_at.

These are real, dated world events (seeded by migration 008 and extendable by
the operator) that contextualise the graph's history during time-travel. The
frontend renders each as a waypoint on the almanac scrubber track.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["events"])


def _pool(request: Request):
    return request.app.state.raw_pool


@router.get("/events")
async def list_events(
    request: Request,
    limit: int = Query(500, ge=1, le=2000),
) -> JSONResponse:
    """
    Return notable events ordered by occurrence time.

    The scrubber positions each event on its track by `occurred_at`, so no
    server-side windowing is needed — the frontend filters to its visible span.
    """
    pool = _pool(request)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_id, occurred_at, label, description, category
            FROM notable_events
            ORDER BY occurred_at
            LIMIT $1
            """,
            limit,
        )

    return JSONResponse({
        "events": [
            {
                "event_id": str(r["event_id"]),
                "occurred_at": r["occurred_at"].isoformat() if r["occurred_at"] else None,
                "label": r["label"],
                "description": r["description"],
                "category": r["category"],
            }
            for r in rows
        ]
    })
