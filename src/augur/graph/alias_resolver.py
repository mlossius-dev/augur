"""
Entity alias resolver.

Resolves a proposed entity name to a canonical node ID using the aliases
table.  Resolution is deterministic and case-insensitive — no LLM calls.

The spec (docs/augur-graph-schema.md, "Entity resolution and aliases"):
- Alias resolution is a deterministic lookup, not an LLM call.
- A normalisation pass runs the proposed name through a case-insensitive alias
  table.  If a match exists, the anchor is rewritten to use the canonical node.
- Alias additions go through the same applier as any other graph mutation.

This module is intentionally stateless: every call fetches from the DB.
A simple LRU cache on the hot path would be fine but is not needed in Phase 1
where call volume is low.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ResolvedAlias:
    """A successful alias lookup result."""

    canonical_node_id: UUID
    canonical_name: str
    matched_alias: str


class AliasResolver:
    """
    Resolves entity names to canonical node IDs using the aliases table.

    Must be constructed with an active asyncpg connection or pool.
    """

    def __init__(self, pool) -> None:  # type: ignore[type-arg]
        self._pool = pool

    async def resolve(self, name: str) -> ResolvedAlias | None:
        """
        Look up `name` in the aliases table (case-insensitive exact match).

        Returns a ResolvedAlias if a match is found, None otherwise.
        A None result means the name is new and a create_node should proceed.
        """
        normalised = name.strip().lower()
        if not normalised:
            return None

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT canonical_node_id, canonical_name, alias_text
                FROM aliases
                WHERE lower(alias_text) = $1
                  AND canonical_node_id IS NOT NULL
                """,
                normalised,
            )

        if row is None:
            return None

        return ResolvedAlias(
            canonical_node_id=UUID(str(row["canonical_node_id"])),
            canonical_name=row["canonical_name"],
            matched_alias=row["alias_text"],
        )

    async def fuzzy_candidates(self, name: str, threshold: float = 0.5) -> list[ResolvedAlias]:
        """
        Return alias entries whose pg_trgm similarity to `name` exceeds `threshold`.

        Used by the operator to detect near-duplicate entities that might need
        manual alias consolidation.  Not called on the hot Applier path.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT canonical_node_id, canonical_name, alias_text,
                       similarity(lower(alias_text), $1) AS sim
                FROM aliases
                WHERE canonical_node_id IS NOT NULL
                  AND similarity(lower(alias_text), $1) >= $2
                ORDER BY sim DESC
                LIMIT 10
                """,
                name.lower(),
                threshold,
            )

        return [
            ResolvedAlias(
                canonical_node_id=UUID(str(r["canonical_node_id"])),
                canonical_name=r["canonical_name"],
                matched_alias=r["alias_text"],
            )
            for r in rows
        ]

    async def register(
        self,
        *,
        alias_text: str,
        canonical_name: str,
        canonical_node_id: UUID | None,
        added_by: str = "applier",
        conn=None,  # type: ignore[type-arg]
    ) -> None:
        """
        Add a new alias entry.  If `canonical_node_id` is None the alias is a
        pre-seed entry (name is known, node doesn't exist yet).

        The alias_text is stored in its original form; lookups are
        case-insensitive via `lower(alias_text) = lower($1)`.
        """
        execute = conn.execute if conn else (await self._pool.acquire()).execute

        async def _run(c) -> None:
            await c.execute(
                """
                INSERT INTO aliases (alias_text, canonical_node_id, canonical_name, added_by)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (alias_text) DO UPDATE
                    SET canonical_node_id = EXCLUDED.canonical_node_id,
                        canonical_name    = EXCLUDED.canonical_name
                    WHERE aliases.canonical_node_id IS NULL
                """,
                alias_text,
                canonical_node_id,
                canonical_name,
                added_by,
            )

        if conn is not None:
            await _run(conn)
        else:
            async with self._pool.acquire() as c:
                await _run(c)

        log.debug(
            "alias.registered",
            alias=alias_text,
            canonical=canonical_name,
            node_id=str(canonical_node_id) if canonical_node_id else None,
        )

    async def attach_node_id(
        self,
        *,
        canonical_name: str,
        canonical_node_id: UUID,
        conn=None,  # type: ignore[type-arg]
    ) -> int:
        """
        Update all pre-seed aliases for `canonical_name` to point at `canonical_node_id`.

        Called by the Applier after a new Entity node is created, so that
        any pre-loaded seed aliases immediately resolve.

        Returns the number of rows updated.
        """
        async def _run(c) -> int:
            result = await c.execute(
                """
                UPDATE aliases
                SET canonical_node_id = $1
                WHERE lower(canonical_name) = lower($2)
                  AND canonical_node_id IS NULL
                """,
                canonical_node_id,
                canonical_name,
            )
            # asyncpg returns "UPDATE N" as a string
            return int(result.split()[-1])

        if conn is not None:
            return await _run(conn)
        else:
            async with self._pool.acquire() as c:
                return await _run(c)
