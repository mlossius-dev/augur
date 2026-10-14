"""
Retention of rejected payloads.

Accepted payloads are kept indefinitely; rejected ones (exact duplicates of
content already ingested, items too short to read) are deleted after a
retention window (docs/augur-signal-pipeline.md, "Retention of rejected
payloads").  They are never archived to the filesystem, so the database row
is all there is to remove.
"""

from __future__ import annotations

import asyncpg
import structlog

log = structlog.get_logger(__name__)


async def prune_rejected_payloads(pool: asyncpg.Pool, *, retention_days: int) -> int:
    """Delete rejected payloads fetched more than `retention_days` ago; return the count."""
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    async with pool.acquire() as conn:
        status = await conn.execute(
            """
            DELETE FROM payloads p
            WHERE p.rejected
              AND p.fetched_at < now() - make_interval(days => $1)
              -- Never orphan a signal, should one exist for a rejected payload
              AND NOT EXISTS (SELECT 1 FROM signals s WHERE s.payload_id = p.payload_id)
            """,
            retention_days,
        )
    deleted = int(status.split()[-1])
    log.info("retention.rejected_payloads_pruned", deleted=deleted, retention_days=retention_days)
    return deleted
