"""
Health check endpoints.

/health        — liveness probe (returns 200 if the process is running)
/health/db     — DB readiness check (exercises pgvector, pg_trgm, PostGIS, AGE)
/health/llm    — LLM connectivity check (Langfuse reachable, keys configured)
/health/ready  — full readiness check combining db + llm
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status

log = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])


def _get_llm_client(request: Request):
    """Retrieve the LLMClient stored on app.state during startup."""
    return request.app.state.llm_client


@router.get("/health", status_code=status.HTTP_200_OK)
async def liveness() -> dict[str, str]:
    """Process liveness — if we can respond, we're alive."""
    return {"status": "ok"}


@router.get("/health/db")
async def db_health() -> dict[str, Any]:
    """
    Exercise pgvector, pg_trgm, PostGIS, and Apache AGE.

    Phase 0 success criterion:
    'A test Postgres query through the application returns expected results,
    exercising at least the pgvector and AGE extensions.'
    """
    from augur.db.connection import get_raw_pool

    pool = get_raw_pool()
    results: dict[str, Any] = {}

    async with pool.acquire() as conn:
        # pgvector
        try:
            row = await conn.fetchrow("SELECT '[1, 2, 3]'::vector AS v")
            results["pgvector"] = {"status": "ok", "sample": str(row["v"])}
        except Exception as exc:
            log.error("health.db.pgvector_failed", error=str(exc))
            results["pgvector"] = {"status": "error", "detail": str(exc)}

        # pg_trgm
        try:
            row = await conn.fetchrow("SELECT similarity('augur', 'auger') AS s")
            results["pg_trgm"] = {"status": "ok", "sample": float(row["s"])}
        except Exception as exc:
            log.error("health.db.pg_trgm_failed", error=str(exc))
            results["pg_trgm"] = {"status": "error", "detail": str(exc)}

        # PostGIS
        try:
            row = await conn.fetchrow("SELECT ST_AsText(ST_Point(10.73, 59.91)) AS p")
            results["postgis"] = {"status": "ok", "sample": row["p"]}
        except Exception as exc:
            log.error("health.db.postgis_failed", error=str(exc))
            results["postgis"] = {"status": "error", "detail": str(exc)}

        # Apache AGE — create + drop a temporary graph
        try:
            await conn.execute("LOAD 'age'")
            await conn.execute("SET search_path = ag_catalog, \"$user\", public")
            await conn.execute("SELECT create_graph('_health_check_graph')")
            await conn.execute("SELECT drop_graph('_health_check_graph', true)")
            results["apache_age"] = {"status": "ok"}
        except Exception as exc:
            log.error("health.db.age_failed", error=str(exc))
            results["apache_age"] = {"status": "error", "detail": str(exc)}

    all_ok = all(v.get("status") == "ok" for v in results.values())
    if not all_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=results,
        )
    return results


@router.get("/health/llm")
async def llm_health(
    llm_client=Depends(_get_llm_client),
) -> dict[str, Any]:
    """Check Langfuse reachability and confirm both OpenRouter keys are configured."""
    result = await llm_client.health_check()
    if not result.get("langfuse_reachable") or not result.get("main_key_configured"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=result,
        )
    return result


@router.get("/health/ready")
async def readiness(
    llm_client=Depends(_get_llm_client),
) -> dict[str, Any]:
    """Full readiness check: DB + LLM + Langfuse."""
    db_result = await db_health()
    llm_result = await llm_client.health_check()
    return {"db": db_result, "llm": llm_result, "status": "ready"}
