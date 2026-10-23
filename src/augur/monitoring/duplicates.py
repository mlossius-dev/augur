"""
Duplicate-node candidates for operator review.

Anchoring resolves names through the alias table, but near-duplicates still
get in: the same headline as an `event` and a `claim`, "Natural Gas" next to
"Natural Gas Supply".  Name similarity alone cannot decide a merge ("Iran"
and "Iran War" are similar and distinct), so this module only lists
candidates with the context an operator needs to judge them.  It never
changes the graph; merging is a separate, operator-approved step.
"""

from __future__ import annotations

from typing import Any

import asyncpg

DEFAULT_MIN_SIMILARITY = 0.45


async def find_duplicate_candidates(
    pool: asyncpg.Pool,
    *,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    Node pairs whose names are trigram-similar, most similar first.

    Each pair carries both nodes' types and edge counts and how many
    neighbours they share: identical names, or shared neighbours, make a
    true duplicate likelier than similarity alone.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            # % uses the trigram index; set_limit applies to this transaction
            await conn.execute("SELECT set_limit($1)", min_similarity)
            rows = await conn.fetch(
                """
                WITH degree AS (
                    SELECT node_id, COUNT(*) AS n_edges
                    FROM (
                        SELECT source_node_id AS node_id FROM edges WHERE NOT deprecated
                        UNION ALL
                        SELECT target_node_id FROM edges WHERE NOT deprecated
                    ) ends
                    GROUP BY node_id
                ),
                neighbours AS (
                    SELECT source_node_id AS node_id, target_node_id AS other
                    FROM edges WHERE NOT deprecated
                    UNION
                    SELECT target_node_id, source_node_id
                    FROM edges WHERE NOT deprecated
                )
                SELECT a.node_id AS a_id, a.name AS a_name, a.node_type AS a_type,
                       b.node_id AS b_id, b.name AS b_name, b.node_type AS b_type,
                       similarity(a.name, b.name) AS similarity,
                       lower(a.name) = lower(b.name) AS same_name,
                       COALESCE(da.n_edges, 0) AS a_edges,
                       COALESCE(db.n_edges, 0) AS b_edges,
                       (SELECT COUNT(*) FROM neighbours na
                          JOIN neighbours nb ON nb.other = na.other
                         WHERE na.node_id = a.node_id AND nb.node_id = b.node_id
                       ) AS shared_neighbours
                FROM nodes a
                JOIN nodes b ON a.node_id < b.node_id AND a.name % b.name
                    AND a.merged_into IS NULL AND b.merged_into IS NULL
                LEFT JOIN degree da ON da.node_id = a.node_id
                LEFT JOIN degree db ON db.node_id = b.node_id
                ORDER BY same_name DESC, similarity DESC
                LIMIT $1
                """,
                limit,
            )

    return [
        {
            "a": {"node_id": str(r["a_id"]), "name": r["a_name"], "type": r["a_type"],
                  "edges": r["a_edges"]},
            "b": {"node_id": str(r["b_id"]), "name": r["b_name"], "type": r["b_type"],
                  "edges": r["b_edges"]},
            "similarity": round(float(r["similarity"]), 2),
            "same_name": r["same_name"],
            "shared_neighbours": r["shared_neighbours"],
        }
        for r in rows
    ]
