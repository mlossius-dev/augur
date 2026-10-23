"""
Topic clustering against a real database (APPLIER_TEST_DATABASE_URL).

docs/augur-presentation.md, "How topics emerge": a run creates clustered
topics, grows existing ones, keeps operator members, and hidden topics
leave every read path, Discourse included.
"""

from __future__ import annotations

import os
import uuid

import pytest

from augur.presentation.clustering import recluster_topics
from augur.presentation.topics import get_topic_list

DSN = os.environ.get("APPLIER_TEST_DATABASE_URL", "")
db = pytest.mark.skipif(not DSN, reason="APPLIER_TEST_DATABASE_URL not set")


class _TestPool:
    """The pool, plus the fixture's clique builder and run tag."""

    def __init__(self, pool, clique, tag) -> None:
        self._pool, self.clique, self.tag = pool, clique, tag

    def __getattr__(self, name):
        return getattr(self._pool, name)


@pytest.fixture
async def pool():
    import asyncpg

    p = await asyncpg.create_pool(
        DSN.replace("postgresql+asyncpg://", "postgresql://"),
        server_settings={"search_path": 'ag_catalog, "$user", public'},
        min_size=1, max_size=2,
    )
    tag = uuid.uuid4().hex[:8]
    created: list[uuid.UUID] = []

    async def clique(name: str, n: int) -> list[uuid.UUID]:
        ids = []
        for i in range(n):
            state = {"current_state": "active"} if i < 3 else {}
            ids.append(await p.fetchval(
                "INSERT INTO nodes (node_type, name, type_data) VALUES ($1, $2, $3::jsonb) RETURNING node_id",
                "condition" if state else "entity", f"{name} {tag} {i}",
                '{"current_state": "active"}' if state else "{}"))
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                await p.execute(
                    """INSERT INTO edges (source_node_id, target_node_id, edge_type, current_weight_band,
                                          reasoning, falsification_criteria)
                       VALUES ($1, $2, 'causes', 'moderate', 't', 't')""", a, b)
        created.extend(ids)
        return ids

    try:
        yield _TestPool(p, clique, tag)
    finally:
        edges = "SELECT edge_id FROM edges WHERE source_node_id = ANY($1) OR target_node_id = ANY($1)"
        await p.execute(f"DELETE FROM edge_weight_history WHERE edge_id IN ({edges})", created)
        await p.execute(f"DELETE FROM edges WHERE edge_id IN ({edges})", created)
        await p.execute(
            "DELETE FROM topics WHERE topic_id IN (SELECT topic_id FROM topic_nodes WHERE node_id = ANY($1))"
            " OR name LIKE $2", created, f"%{tag}%")
        await p.execute("DELETE FROM nodes WHERE node_id = ANY($1)", created)
        await p.close()


async def _topic_of(pool, node_id) -> dict | None:
    row = await pool.fetchrow(
        """SELECT t.topic_id, t.name, t.origin, tn.added_by FROM topic_nodes tn
           JOIN topics t USING (topic_id) WHERE tn.node_id = $1""", node_id)
    return dict(row) if row else None


@db
@pytest.mark.asyncio
async def test_a_dense_community_becomes_a_clustered_topic(pool) -> None:
    ids = await pool.clique("Oil", 12)
    await recluster_topics(pool, apply=True)
    t = await _topic_of(pool, ids[5])
    assert t and t["origin"] == "clustered" and t["added_by"] == "clustering"
    assert pool.tag in t["name"]


@db
@pytest.mark.asyncio
async def test_an_operator_topic_grows_and_keeps_its_own_members(pool) -> None:
    ids = await pool.clique("War", 12)
    tid = await pool.fetchval("INSERT INTO topics (name) VALUES ($1) RETURNING topic_id", f"War {pool.tag}")
    await pool.executemany("INSERT INTO topic_nodes (topic_id, node_id) VALUES ($1, $2)",
                           [(tid, n) for n in ids[:4]])
    await recluster_topics(pool, apply=True)
    rows = await pool.fetch("SELECT node_id, added_by FROM topic_nodes WHERE topic_id = $1", tid)
    by = {r["node_id"]: r["added_by"] for r in rows}
    assert set(by) == set(ids), "the topic now holds its whole community"
    assert all(by[n] == "operator" for n in ids[:4])
    assert (await pool.fetchval("SELECT origin FROM topics WHERE topic_id = $1", tid)) == "operator"
    assert (await pool.fetchval("SELECT name FROM topics WHERE topic_id = $1", tid)) == f"War {pool.tag}"


@db
@pytest.mark.asyncio
async def test_a_hidden_topic_is_not_recreated_and_leaves_every_read_path(pool) -> None:
    ids = await pool.clique("Sport", 12)
    await recluster_topics(pool, apply=True)
    tid = (await _topic_of(pool, ids[0]))["topic_id"]
    await pool.execute("UPDATE topics SET hidden = TRUE WHERE topic_id = $1", tid)

    await recluster_topics(pool, apply=True)
    n = await pool.fetchval(
        "SELECT count(DISTINCT topic_id) FROM topic_nodes WHERE node_id = ANY($1)", ids)
    assert n == 1, "no second topic for the same community"
    assert tid not in {t.topic_id for t in await get_topic_list(pool)}
    assert not await pool.fetchval("SELECT count(*) FROM core_bridge.topics WHERE topic_id = $1", tid)
    assert not await pool.fetchval("SELECT count(*) FROM core_bridge.topic_nodes WHERE topic_id = $1", tid)


@db
@pytest.mark.asyncio
async def test_a_dry_run_writes_nothing(pool) -> None:
    ids = await pool.clique("Grain", 12)
    plans = await recluster_topics(pool, apply=False)
    assert any(ids[0] in {uuid.UUID(m) for m in p.members} for p in plans)
    assert await _topic_of(pool, ids[0]) is None
