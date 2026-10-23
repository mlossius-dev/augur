"""
merge_nodes against a real database (APPLIER_TEST_DATABASE_URL).

docs/augur-graph-schema.md, "Merging duplicate nodes": every step of the
merge, every rejection, and that no LLM output can parse into a merge.
Point APPLIER_TEST_DATABASE_URL at a disposable, migrated database.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from augur.graph.applier import (
    REJECT_MERGE_ALREADY_MERGED,
    REJECT_MERGE_SAME_NODE,
    REJECT_MERGE_TYPE_CHANGE,
    REJECT_UNRESOLVED_TARGET,
    Applier,
)
from augur.graph.models import (
    AddSupportingSignalOperation,
    CreateEdgeOperation,
    CreateNodeOperation,
    MergeNodesOperation,
    ProposedAnchorOperation,
    UpdateNodeOperation,
)

DSN = os.environ.get("APPLIER_TEST_DATABASE_URL", "")

JAN = datetime(2026, 1, 15, tzinfo=UTC)
MAR = datetime(2026, 3, 15, tzinfo=UTC)
OCT = datetime(2026, 10, 15, tzinfo=UTC)


def test_no_anchor_output_can_be_a_merge() -> None:
    """merge_nodes is operator-only: the anchoring union rejects it."""
    adapter = TypeAdapter(ProposedAnchorOperation)
    with pytest.raises(ValidationError):
        adapter.validate_python({
            "operation": "merge_nodes", "survivor_id": str(uuid.uuid4()),
            "absorbed_id": str(uuid.uuid4()), "reasoning": "same thing",
        })


db = pytest.mark.skipif(not DSN, reason="APPLIER_TEST_DATABASE_URL not set")


@pytest.fixture
async def g():
    import asyncpg

    pool = await asyncpg.create_pool(
        DSN.replace("postgresql+asyncpg://", "postgresql://"),
        server_settings={"search_path": 'ag_catalog, "$user", public'},
        min_size=1, max_size=2,
    )
    graph = _Graph(pool)
    try:
        yield graph
    finally:
        await graph.cleanup()
        await pool.close()


class _Graph:
    def __init__(self, pool) -> None:
        self.pool = pool
        self.applier = Applier(pool)
        self.nodes: list[uuid.UUID] = []

    async def node(self, node_type="condition", name=None, ts=MAR, **fields) -> uuid.UUID:
        name = name or f"Test {node_type} {uuid.uuid4().hex[:8]}"
        base = {
            "condition": {"current_state": "active"},
            "entity": {"entity_kind": "organization"},
            "event": {"occurred_at": "2026-03-15", "event_kind": "economic"},
        }[node_type]
        op = CreateNodeOperation(node_type=node_type, proposed_id="n",
                                 fields={"name": name, **base, **fields}, reasoning="t")
        result = await self.applier.apply([op], content_timestamp=ts, source="test")
        assert not result.rejected, result.rejected
        node_id = result.applied[0].target_node_id
        self.nodes.append(node_id)
        return node_id

    async def edge(self, src, tgt, band="provisional", ts=MAR, edge_type="causes") -> uuid.UUID:
        op = CreateEdgeOperation(source_node_id=str(src), target_node_id=str(tgt),
                                 edge_type=edge_type, proposed_weight_band=band,
                                 reasoning="t", falsification_criteria="t")
        result = await self.applier.apply([op], content_timestamp=ts, source="test")
        assert not result.rejected, result.rejected
        return result.applied[0].target_edge_id

    async def merge(self, survivor, absorbed, **kw):
        return await self.applier.merge_nodes(MergeNodesOperation(
            survivor_id=str(survivor), absorbed_id=str(absorbed), reasoning="duplicate", **kw),
            source="test")

    async def cleanup(self) -> None:
        p, ids = self.pool, self.nodes
        await p.execute("UPDATE nodes SET merged_into = NULL WHERE node_id = ANY($1)", ids)
        await p.execute("DELETE FROM graph_update_events WHERE source = 'test'")
        edges = "SELECT edge_id FROM edges WHERE source_node_id = ANY($1) OR target_node_id = ANY($1)"
        await p.execute(f"DELETE FROM edge_weight_history WHERE edge_id IN ({edges})", ids)
        await p.execute(f"DELETE FROM edges WHERE edge_id IN ({edges})", ids)
        for table in ("condition_state_history", "claim_assessment_history", "topic_nodes"):
            await p.execute(f"DELETE FROM {table} WHERE node_id = ANY($1)", ids)
        await p.execute("DELETE FROM aliases WHERE canonical_node_id = ANY($1)", ids)
        await p.execute("DELETE FROM nodes WHERE node_id = ANY($1)", ids)


# ── The merge itself ─────────────────────────────────────────────────────────


@db
@pytest.mark.asyncio
async def test_edges_move_and_the_absorbed_node_becomes_a_tombstone(g) -> None:
    keep, dup, other = await g.node(), await g.node(), await g.node()
    edge = await g.edge(dup, other)

    event = await g.merge(keep, dup)

    assert not event.rejected
    row = await g.pool.fetchrow("SELECT source_node_id, deprecated FROM edges WHERE edge_id = $1", edge)
    assert (row["source_node_id"], row["deprecated"]) == (keep, False)
    tomb = await g.pool.fetchrow("SELECT merged_into, merged_at FROM nodes WHERE node_id = $1", dup)
    assert tomb["merged_into"] == keep and tomb["merged_at"] is not None, "kept, not deleted"
    logged = await g.pool.fetchrow(
        "SELECT event_type, target_node_id, operation_data FROM graph_update_events "
        "WHERE event_id = $1", event.event_id)
    assert (logged["event_type"], logged["target_node_id"]) == ("merge_nodes", keep)
    assert str(edge) in logged["operation_data"], "the snapshot records what moved"


@db
@pytest.mark.asyncio
async def test_an_edge_between_the_two_becomes_a_deprecated_self_loop(g) -> None:
    keep, dup = await g.node(), await g.node()
    edge = await g.edge(dup, keep)
    await g.merge(keep, dup)
    assert await g.pool.fetchval("SELECT deprecated FROM edges WHERE edge_id = $1", edge)


@db
@pytest.mark.asyncio
async def test_duplicate_edges_collapse_into_the_strongest_without_reweighting(g) -> None:
    keep, dup, other = await g.node(), await g.node(), await g.node()
    weak = await g.edge(keep, other, band="weak", ts=MAR)
    strong = await g.edge(dup, other, band="moderate", ts=JAN)
    signal = uuid.uuid4()
    await g.applier.apply([AddSupportingSignalOperation(
        target_edge_id=str(weak), signal_id=str(signal))], content_timestamp=MAR, source="test")

    await g.merge(keep, dup)

    rows = {r["edge_id"]: r for r in await g.pool.fetch(
        "SELECT edge_id, deprecated, current_weight_band, supporting_signals, created_at "
        "FROM edges WHERE edge_id = ANY($1)", [weak, strong])}
    assert rows[weak]["deprecated"] and not rows[strong]["deprecated"]
    assert rows[strong]["current_weight_band"] == "moderate", "no weight change"
    assert signal in rows[strong]["supporting_signals"], "evidence carried over"
    assert rows[strong]["created_at"] == JAN


@db
@pytest.mark.asyncio
async def test_history_moves_and_the_latest_state_by_content_time_wins(g) -> None:
    keep = await g.node(current_state="active", ts=JAN)
    dup = await g.node(current_state="active", ts=MAR)
    await g.applier.apply([UpdateNodeOperation(
        target_node_id=str(dup), field_updates={"current_state": "inactive"}, reasoning="t")],
        content_timestamp=OCT, source="test")

    await g.merge(keep, dup)

    assert await g.pool.fetchval(
        "SELECT count(*) FROM condition_state_history WHERE node_id = $1", keep) == 3
    assert await g.pool.fetchval(
        "SELECT type_data->>'current_state' FROM nodes WHERE node_id = $1", keep) == "inactive"


@db
@pytest.mark.asyncio
async def test_provenance_reaches_back_to_the_earlier_node(g) -> None:
    keep, dup = await g.node(ts=OCT), await g.node(ts=JAN)
    await g.merge(keep, dup)
    assert await g.pool.fetchval("SELECT created_at FROM nodes WHERE node_id = $1", keep) == JAN


@db
@pytest.mark.asyncio
async def test_the_absorbed_name_resolves_to_the_survivor(g) -> None:
    keep = await g.node("entity")
    dup_name = f"Test entity {uuid.uuid4().hex[:8]}"
    dup = await g.node("entity", name=dup_name)
    await g.merge(keep, dup)

    result = await g.applier.apply([CreateNodeOperation(
        node_type="entity", proposed_id="x",
        fields={"name": dup_name, "entity_kind": "organization"}, reasoning="t")],
        content_timestamp=OCT, source="test")
    assert result.applied[0].event_type == "create_node_aliased"
    assert result.applied[0].target_node_id == keep


@db
@pytest.mark.asyncio
async def test_a_reference_to_the_absorbed_id_lands_on_the_survivor(g) -> None:
    keep, dup, other = await g.node(), await g.node(), await g.node()
    await g.merge(keep, dup)
    edge = await g.edge(dup, other, ts=OCT)  # e.g. anchoring with stale context
    assert await g.pool.fetchval(
        "SELECT source_node_id FROM edges WHERE edge_id = $1", edge) == keep


@db
@pytest.mark.asyncio
async def test_topic_membership_moves_without_duplicating(g) -> None:
    keep, dup = await g.node(), await g.node()
    topic = await g.pool.fetchval(
        "INSERT INTO topics (name, dimension) VALUES ($1, 'economic_stability') RETURNING topic_id",
        f"Test topic {uuid.uuid4().hex[:8]}")
    try:
        await g.pool.execute(
            "INSERT INTO topic_nodes (topic_id, node_id) VALUES ($1, $2), ($1, $3)", topic, keep, dup)
        await g.merge(keep, dup)
        members = [r["node_id"] for r in await g.pool.fetch(
            "SELECT node_id FROM topic_nodes WHERE topic_id = $1", topic)]
        assert members == [keep]
    finally:
        await g.pool.execute("DELETE FROM topics WHERE topic_id = $1", topic)


# ── Rejections ───────────────────────────────────────────────────────────────


@db
@pytest.mark.asyncio
async def test_a_node_cannot_absorb_itself(g) -> None:
    n = await g.node()
    event = await g.merge(n, n)
    assert event.rejection_reason == REJECT_MERGE_SAME_NODE


@db
@pytest.mark.asyncio
async def test_an_unknown_node_is_rejected(g) -> None:
    event = await g.merge(await g.node(), uuid.uuid4())
    assert event.rejection_reason == REJECT_UNRESOLVED_TARGET


@db
@pytest.mark.asyncio
async def test_an_already_merged_node_is_rejected(g) -> None:
    a, b, c = await g.node(), await g.node(), await g.node()
    await g.merge(a, b)
    assert (await g.merge(c, b)).rejection_reason == REJECT_MERGE_ALREADY_MERGED
    assert (await g.merge(b, c)).rejection_reason == REJECT_MERGE_ALREADY_MERGED


@db
@pytest.mark.asyncio
async def test_different_types_need_the_flag(g) -> None:
    condition, event = await g.node("condition"), await g.node("event")
    rejected = await g.merge(condition, event)
    assert rejected.rejection_reason == REJECT_MERGE_TYPE_CHANGE
    assert not await g.pool.fetchval(
        "SELECT merged_into FROM nodes WHERE node_id = $1", event), "nothing changed"

    assert not (await g.merge(condition, event, allow_type_change=True)).rejected


@db
@pytest.mark.asyncio
async def test_a_node_with_state_history_never_changes_type(g) -> None:
    event, condition = await g.node("event"), await g.node("condition")
    result = await g.merge(event, condition, allow_type_change=True)
    assert result.rejection_reason == REJECT_MERGE_TYPE_CHANGE
