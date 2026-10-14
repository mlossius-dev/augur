"""
Rejected-payload retention against a migrated database (DATABASE_URL).

Only rejected payloads past the window are deleted; accepted payloads and
recent rejections stay.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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


async def _insert(pool, *, age_days: int, rejected: bool):
    payload_id = uuid4()
    fetched_at = datetime.now(UTC) - timedelta(days=age_days)
    await pool.execute(
        """
        INSERT INTO payloads
            (payload_id, source_id, fetched_at, content_timestamp, perspective,
             content, content_type, language, metadata, rejected, rejected_reason)
        VALUES ($1, 'retention_test', $2, $2, 'us_eu', 'x', 'article', 'en', $3, $4, $5)
        """,
        payload_id,
        fetched_at,
        json.dumps({}),
        rejected,
        "test" if rejected else None,
    )
    return payload_id


@pytest.mark.asyncio
async def test_only_old_rejected_payloads_are_deleted(pool) -> None:
    from augur.ingestion.retention import prune_rejected_payloads

    old_rejected = await _insert(pool, age_days=40, rejected=True)
    new_rejected = await _insert(pool, age_days=5, rejected=True)
    old_accepted = await _insert(pool, age_days=40, rejected=False)
    try:
        deleted = await prune_rejected_payloads(pool, retention_days=30)

        remaining = {
            r["payload_id"]
            for r in await pool.fetch(
                "SELECT payload_id FROM payloads WHERE source_id = 'retention_test'"
            )
        }
        assert deleted >= 1
        assert old_rejected not in remaining
        assert {new_rejected, old_accepted} <= remaining
    finally:
        await pool.execute("DELETE FROM payloads WHERE source_id = 'retention_test'")


@pytest.mark.asyncio
async def test_retention_must_be_positive(pool) -> None:
    from augur.ingestion.retention import prune_rejected_payloads

    with pytest.raises(ValueError):
        await prune_rejected_payloads(pool, retention_days=0)
