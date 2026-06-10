"""
System status API — exposes pipeline health for the live header strip.

GET /api/status → current graph size, signal flow, and pipeline health.

Only genuine windows are returned. The frontend must not simulate or
extrapolate beyond what this endpoint provides.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from augur.monitoring.health import get_pipeline_health, get_signal_flow

router = APIRouter(prefix="/api", tags=["status"])


def _pool(request: Request):
    return request.app.state.raw_pool


@router.get("/status")
async def system_status(request: Request) -> JSONResponse:
    """
    Live system metrics for the header strip.

    Available windows:
      payloads  — 24h total/rejected (from payloads table)
      signals   — 1h and 24h counts (from signals table)
      graph     — current live node/edge/strong-edge totals
      pipeline  — anchoring backlog and stale-for-disconfirmation counts
    """
    pool = _pool(request)

    health = await get_pipeline_health(pool)
    flow = await get_signal_flow(pool, hours=1)

    return JSONResponse({
        "payloads": {
            "last_24h": health["payloads_24h"]["total"],
            "rejected_24h": health["payloads_24h"]["rejected"],
        },
        "signals": {
            "last_1h": health["signals"]["last_hour"],
            "last_24h": health["signals"]["last_24h"],
            "total": health["signals"]["total"],
        },
        "graph": {
            "live_nodes": health["graph"]["live_nodes"],
            "live_edges": health["graph"]["live_edges"],
            "strong_edges": health["graph"]["strong_edges"],
            "disputed_edges": health["graph"]["disputed_edges"],
        },
        "pipeline": {
            "anchoring_backlog": health["pipeline"]["anchoring_backlog"],
            "stale_for_disconfirmation": health["pipeline"]["stale_edges_for_disconfirmation"],
        },
        "signal_flow_1h": {
            "total": flow["total_signals"],
            "unique_clusters": flow["unique_clusters"],
            "by_lens": flow["by_lens"],
        },
        "generated_at": health["generated_at"],
    })
