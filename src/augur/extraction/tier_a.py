"""
Tier A — raw signal store.

Stores signals, manages cluster assignments, and provides query interfaces
for the anchoring stage and operator UI.

Phase 2 scope: store + basic intra-lens deduplication (hash-based).
Phase 3+ adds pgvector-based similarity clustering when the embedding
pipeline is in place.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg
import structlog

log = structlog.get_logger(__name__)


class TierAStore:
    """
    Read/write access to the Tier A signal table.

    Constructed with an asyncpg pool.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def store_signals(
        self, signals: list[dict[str, Any]]
    ) -> int:
        """
        Bulk-insert a list of signal dicts (output of LensExecutor.extract).

        Returns the number of signals actually inserted (skips duplicates
        detected by signal_hash deduplication).
        """
        if not signals:
            return 0

        inserted = 0
        async with self._pool.acquire() as conn:
            for signal in signals:
                ok = await self._insert_signal(signal, conn)
                if ok:
                    inserted += 1

        log.info("tier_a.stored", n_signals=inserted, n_attempted=len(signals))
        return inserted

    async def store_signal(self, signal: dict[str, Any]) -> bool:
        """Insert a single signal. Returns True if inserted."""
        async with self._pool.acquire() as conn:
            return await self._insert_signal(signal, conn)

    async def get_unanchored(
        self,
        *,
        lens_id: str | None = None,
        min_age_hours: int = 1,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Return unanchored signals older than `min_age_hours`.

        Used by the anchoring stage to pull batches ready for graph mutation.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=min_age_hours)
        params: list[Any] = [cutoff, limit]
        conditions = ["NOT anchored", "extracted_at < $1"]

        if lens_id is not None:
            conditions.append(f"lens_id = ${len(params) + 1}")
            params.append(lens_id)

        where = " AND ".join(conditions)
        sql = (
            f"SELECT * FROM signals WHERE {where} "
            f"ORDER BY content_timestamp DESC LIMIT $2"
        )

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [_row_to_dict(r) for r in rows]

    async def get_recent(
        self,
        *,
        lens_id: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent signals for operator inspection."""
        conditions: list[str] = []
        params: list[Any] = [limit]

        if lens_id:
            conditions.append(f"lens_id = ${len(params) + 1}")
            params.append(lens_id)

        if source_id:
            # Join with payloads to filter by source_id
            conditions.append(
                f"payload_id IN (SELECT payload_id FROM payloads WHERE source_id = ${len(params) + 1})"
            )
            params.append(source_id)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM signals {where} ORDER BY extracted_at DESC LIMIT $1"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        return [_row_to_dict(r) for r in rows]

    async def mark_anchored(
        self, signal_ids: list[UUID], *, conn: asyncpg.Connection | None = None
    ) -> None:
        """Mark signals as anchored after the applier processes them."""
        if not signal_ids:
            return
        sql = "UPDATE signals SET anchored = TRUE WHERE signal_id = ANY($1)"
        if conn is not None:
            await conn.execute(sql, signal_ids)
        else:
            async with self._pool.acquire() as c:
                await c.execute(sql, signal_ids)

    async def assign_cluster(
        self,
        signal_ids: list[UUID],
        cluster_id: UUID,
        *,
        cluster_strength: float = 1.0,
    ) -> None:
        """Assign a cluster ID to a set of signals (intra-lens deduplication)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE signals
                SET cluster_id = $1, cluster_strength = $2
                WHERE signal_id = ANY($3)
                """,
                cluster_id,
                cluster_strength,
                signal_ids,
            )

    async def deduplicate_batch(
        self, signals: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Within a batch of signals from the same lens, detect duplicates by
        claim_text hash and return only unique signals.

        Phase 2: hash-based. Phase 3+: vector similarity via pgvector.
        """
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for sig in signals:
            h = _claim_hash(sig["claim_text"])
            if h not in seen:
                seen.add(h)
                unique.append(sig)
        return unique

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _insert_signal(
        self, signal: dict[str, Any], conn: asyncpg.Connection
    ) -> bool:
        """Insert one signal. Returns False on duplicate (same signal_id)."""
        try:
            await conn.execute(
                """
                INSERT INTO signals
                    (signal_id, payload_id, lens_id, lens_version,
                     claim_text, confidence_band,
                     proposed_anchors, reasoning,
                     content_timestamp, extracted_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                signal["signal_id"],
                signal["payload_id"],
                signal["lens_id"],
                signal["lens_version"],
                signal["claim_text"],
                signal["confidence_band"],
                json.dumps(signal.get("proposed_anchors", [])),
                signal.get("reasoning"),
                signal["content_timestamp"],
                signal["extracted_at"],
            )
            return True
        except asyncpg.UniqueViolationError:
            return False


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    d = dict(row)
    # Deserialize JSONB fields
    if isinstance(d.get("proposed_anchors"), str):
        d["proposed_anchors"] = json.loads(d["proposed_anchors"])
    return d


def _claim_hash(claim_text: str) -> str:
    return hashlib.sha256(claim_text.strip().lower().encode()).hexdigest()[:16]
