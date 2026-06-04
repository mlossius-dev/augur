"""
Source weight override persistence.

Stores operator-approved calibration weight updates in the DB so they
survive restarts and inform ingestion source weighting.

The effective weight for a source is:
  1. The most recent non-superseded source_weight_overrides row, if any.
  2. Otherwise, the starting_source_weight from sources.yaml.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import structlog

log = structlog.get_logger(__name__)


async def persist_weight_overrides(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    updates: dict[str, float],
    applied_by: str = "operator",
    notes: str = "",
) -> int:
    """
    Write calibration weight updates to source_weight_overrides.

    Supersedes any existing non-superseded override for each source_id
    before inserting the new row, maintaining a clean history.

    Returns the number of rows written.
    """
    if not updates:
        return 0

    now = datetime.now(timezone.utc)
    written = 0

    async with pool.acquire() as conn:
        async with conn.transaction():
            for source_id, weight in updates.items():
                # Supersede existing active override for this source
                await conn.execute(
                    """
                    UPDATE source_weight_overrides
                    SET superseded_at = $1
                    WHERE source_id = $2 AND superseded_at IS NULL
                    """,
                    now,
                    source_id,
                )

                await conn.execute(
                    """
                    INSERT INTO source_weight_overrides
                        (source_id, weight, calibration_run_id, applied_at,
                         applied_by, notes)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    source_id,
                    weight,
                    run_id,
                    now,
                    applied_by,
                    notes,
                )
                written += 1

    log.info(
        "weight_store.persisted",
        run_id=str(run_id),
        n_sources=written,
    )
    return written


async def load_all_overrides(pool: asyncpg.Pool) -> dict[str, float]:
    """
    Return the current active weight override for every source that has one.

    Returns {source_id: weight}. Sources with no override are absent.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (source_id) source_id, weight
            FROM source_weight_overrides
            WHERE superseded_at IS NULL
            ORDER BY source_id, applied_at DESC
            """
        )
    return {row["source_id"]: row["weight"] for row in rows}


async def get_effective_weight(
    pool: asyncpg.Pool,
    source_id: str,
    yaml_weight: float,
) -> float:
    """
    Return the effective weight for a source: DB override if present, else YAML baseline.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT weight FROM source_weight_overrides
            WHERE source_id = $1 AND superseded_at IS NULL
            ORDER BY applied_at DESC
            LIMIT 1
            """,
            source_id,
        )
    return float(row["weight"]) if row else yaml_weight


async def override_history(
    pool: asyncpg.Pool,
    source_id: str,
    *,
    limit: int = 20,
) -> list[dict]:
    """Return the weight override history for a source, newest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT weight, calibration_run_id, applied_at, applied_by,
                   notes, superseded_at
            FROM source_weight_overrides
            WHERE source_id = $1
            ORDER BY applied_at DESC
            LIMIT $2
            """,
            source_id,
            limit,
        )
    return [dict(r) for r in rows]
