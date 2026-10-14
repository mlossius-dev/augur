"""
Monitoring queries against a migrated database (DATABASE_URL).

The monitoring module is read-only SQL over the pipeline tables; a column
name that does not exist only fails when the query runs.  These tests run
every read function once, so a schema mismatch fails here instead of as a
500 from /api/status.
"""

from __future__ import annotations

import os

import pytest

from tests.conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db()]


@pytest.fixture
async def pool():
    import asyncpg

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(
        dsn, server_settings={"search_path": 'ag_catalog, "$user", public'}
    )
    yield pool
    await pool.close()


@pytest.mark.asyncio
async def test_pipeline_health_runs(pool) -> None:
    from augur.monitoring.health import get_pipeline_health

    health = await get_pipeline_health(pool)
    assert health["graph"]["live_nodes"] >= 0
    assert health["pipeline"]["anchoring_backlog"] >= 0


@pytest.mark.asyncio
async def test_signal_flow_runs(pool) -> None:
    from augur.monitoring.health import get_signal_flow

    flow = await get_signal_flow(pool, hours=24)
    assert flow["total_signals"] == sum(flow["by_lens"].values())


@pytest.mark.asyncio
async def test_anchoring_quality_runs(pool) -> None:
    from augur.monitoring.health import get_anchoring_quality

    batches = await get_anchoring_quality(pool, limit=5)
    for batch in batches:
        assert batch["n_events"] == batch["n_applied"] + batch["n_rejected"]
