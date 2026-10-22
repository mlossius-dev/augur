"""
The Applier's time-order rule against a real database (APPLIER_TEST_DATABASE_URL).

docs/augur-graph-schema.md, "Time order versus processing order": "current"
is the latest by content_timestamp, never the latest processed.  These tests
apply operations out of order — as a historical backfill does, newest day
first — and check that the graph says the same about every moment as it
would have in order.

Point APPLIER_TEST_DATABASE_URL at a disposable, migrated database: the
tests write nodes and edges and remove only what they created.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

from augur.graph.applier import Applier
from augur.graph.models import (
    AddSupportingSignalOperation,
    CreateEdgeOperation,
    CreateNodeOperation,
    UpdateEdgeWeightOperation,
    UpdateNodeOperation,
)

DSN = os.environ.get("APPLIER_TEST_DATABASE_URL", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="APPLIER_TEST_DATABASE_URL not set"),
]

JAN = datetime(2026, 1, 15, tzinfo=UTC)
MAR = datetime(2026, 3, 15, tzinfo=UTC)
OCT = datetime(2026, 10, 15, tzinfo=UTC)


class _Db:
    """The pool, plus the nodes a test created (removed afterwards)."""

    def __init__(self, pool) -> None:
        self.pool = pool
        self.created: list[uuid.UUID] = []

    def __getattr__(self, name):  # fetch, fetchrow, fetchval, execute
        return getattr(self.pool, name)


@pytest.fixture
async def pool():
    import asyncpg

    raw = await asyncpg.create_pool(
        DSN.replace("postgresql+asyncpg://", "postgresql://"),
        server_settings={"search_path": 'ag_catalog, "$user", public'},
        min_size=1, max_size=2,
    )
    pool = _Db(raw)
    created = pool.created
    try:
        yield pool
    finally:
        await _cleanup(raw, created)
        await raw.close()


async def _cleanup(pool, created: list[uuid.UUID]) -> None:
    # Remove what the test created (edges first, then nodes)
    await pool.execute(
        "DELETE FROM graph_update_events WHERE target_node_id = ANY($1) "
        "OR target_edge_id IN (SELECT edge_id FROM edges "
        "  WHERE source_node_id = ANY($1) OR target_node_id = ANY($1))", created)
    await pool.execute(
        "DELETE FROM edge_weight_history WHERE edge_id IN (SELECT edge_id FROM edges "
        "  WHERE source_node_id = ANY($1) OR target_node_id = ANY($1))", created)
    await pool.execute(
        "DELETE FROM edges WHERE source_node_id = ANY($1) OR target_node_id = ANY($1)", created)
    for table in ("condition_state_history", "claim_assessment_history"):
        await pool.execute(f"DELETE FROM {table} WHERE node_id = ANY($1)", created)
    await pool.execute("DELETE FROM aliases WHERE canonical_node_id = ANY($1)", created)
    await pool.execute("DELETE FROM nodes WHERE node_id = ANY($1)", created)


async def _create(pool, op, ts) -> uuid.UUID:
    result = await Applier(pool.pool).apply([op], content_timestamp=ts, source="test")
    assert not result.rejected, result.rejected
    node_id = result.applied[0].target_node_id
    pool.created.append(node_id)
    return node_id


def _condition(state: str = "active") -> CreateNodeOperation:
    name = f"Test condition {uuid.uuid4().hex[:8]}"
    return CreateNodeOperation(
        node_type="condition", proposed_id="c",
        fields={"name": name, "current_state": state}, reasoning="test",
    )


def _entity(name: str | None = None) -> CreateNodeOperation:
    return CreateNodeOperation(
        node_type="entity", proposed_id="e",
        fields={"name": name or f"Test entity {uuid.uuid4().hex[:8]}", "entity_kind": "organization"},
        reasoning="test",
    )


async def _state(pool, node_id):
    return await pool.fetchval(
        "SELECT type_data->>'current_state' FROM nodes WHERE node_id = $1", node_id)


async def _set_state(pool, node_id, state, ts):
    op = UpdateNodeOperation(
        target_node_id=str(node_id), field_updates={"current_state": state}, reasoning="test")
    result = await Applier(pool.pool).apply([op], content_timestamp=ts, source="test")
    assert not result.rejected, result.rejected


async def _history(pool, node_id):
    return [tuple(r) for r in await pool.fetch(
        "SELECT content_timestamp, new_state, previous_state FROM condition_state_history "
        "WHERE node_id = $1 ORDER BY content_timestamp", node_id)]


# ── Condition state ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_newer_state_update_changes_current(pool) -> None:
    node = await _create(pool, _condition("active"), MAR)
    await _set_state(pool, node, "inactive", OCT)

    assert await _state(pool, node) == "inactive"
    assert (await _history(pool, node))[-1] == (OCT, "inactive", "active")


@pytest.mark.asyncio
async def test_older_state_update_goes_to_history_only(pool) -> None:
    node = await _create(pool, _condition("active"), OCT)
    await _set_state(pool, node, "inactive", MAR)

    assert await _state(pool, node) == "active", "an older update must not override"
    # Nothing was in force before the condition's October creation
    assert await _history(pool, node) == [(MAR, "inactive", None), (OCT, "active", None)]


@pytest.mark.asyncio
async def test_update_between_two_entries_records_the_state_then(pool) -> None:
    node = await _create(pool, _condition("active"), JAN)
    await _set_state(pool, node, "inactive", OCT)
    await _set_state(pool, node, "partially_active", MAR)  # arrives last

    assert await _state(pool, node) == "inactive"
    assert (MAR, "partially_active", "active") in await _history(pool, node)


@pytest.mark.asyncio
async def test_processing_order_does_not_change_the_outcome(pool) -> None:
    forward = await _create(pool, _condition("active"), JAN)
    await _set_state(pool, forward, "partially_active", MAR)
    await _set_state(pool, forward, "inactive", OCT)

    backward = await _create(pool, _condition("active"), JAN)
    await _set_state(pool, backward, "inactive", OCT)
    await _set_state(pool, backward, "partially_active", MAR)

    assert await _state(pool, forward) == await _state(pool, backward) == "inactive"
    as_of_april = (
        "SELECT new_state FROM condition_state_history WHERE node_id = $1 "
        "AND content_timestamp <= '2026-04-01' ORDER BY content_timestamp DESC LIMIT 1")
    assert await pool.fetchval(as_of_april, forward) == await pool.fetchval(
        as_of_april, backward) == "partially_active"


# ── Plain fields and timestamps ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_older_description_does_not_overwrite_newer(pool) -> None:
    node = await _create(pool, _condition(), MAR)
    for text, ts in (("October text", OCT), ("January text", JAN)):
        op = UpdateNodeOperation(
            target_node_id=str(node), field_updates={"description": text}, reasoning="t")
        await Applier(pool.pool).apply([op], content_timestamp=ts, source="test")

    row = await pool.fetchrow(
        "SELECT description, created_at, updated_at FROM nodes WHERE node_id = $1", node)
    assert row["description"] == "October text"
    assert row["created_at"] == JAN, "existence reaches back to the earliest evidence"
    assert row["updated_at"] == OCT, "updated_at never moves backwards"


@pytest.mark.asyncio
async def test_aliased_entity_reaches_back(pool) -> None:
    name = f"Test entity {uuid.uuid4().hex[:8]}"
    node = await _create(pool, _entity(name), OCT)
    result = await Applier(pool.pool).apply([_entity(name)], content_timestamp=MAR, source="test")

    assert result.applied[0].event_type == "create_node_aliased"
    assert await pool.fetchval("SELECT created_at FROM nodes WHERE node_id = $1", node) == MAR


# ── Edges ────────────────────────────────────────────────────────────────────


async def _edge(pool, src, tgt, ts, band="provisional") -> uuid.UUID:
    op = CreateEdgeOperation(
        source_node_id=str(src), target_node_id=str(tgt), edge_type="causes",
        proposed_weight_band=band, reasoning="test", falsification_criteria="test")
    result = await Applier(pool.pool).apply([op], content_timestamp=ts, source="test")
    assert not result.rejected, result.rejected
    return result.applied[0].target_edge_id


async def _weight(pool, edge_id, band, ts):
    op = UpdateEdgeWeightOperation(
        target_edge_id=str(edge_id), new_weight_band=band, direction="strengthen", reasoning="t")
    result = await Applier(pool.pool).apply([op], content_timestamp=ts, source="test")
    assert not result.rejected, result.rejected


@pytest.mark.asyncio
async def test_edge_reaches_its_endpoints_back(pool) -> None:
    a = await _create(pool, _condition(), OCT)
    b = await _create(pool, _condition(), OCT)
    await _edge(pool, a, b, MAR)

    created = await pool.fetch("SELECT created_at FROM nodes WHERE node_id = ANY($1)", [a, b])
    assert {r["created_at"] for r in created} == {MAR}


@pytest.mark.asyncio
async def test_older_weight_update_goes_to_history_only(pool) -> None:
    a = await _create(pool, _condition(), JAN)
    b = await _create(pool, _condition(), JAN)
    edge = await _edge(pool, a, b, OCT, band="provisional")
    await _weight(pool, edge, "strong", MAR)

    row = await pool.fetchrow(
        "SELECT current_weight_band, created_at FROM edges WHERE edge_id = $1", edge)
    assert row["current_weight_band"] == "provisional"
    assert row["created_at"] == MAR
    entry = await pool.fetchrow(
        "SELECT weight_band, previous_weight_band FROM edge_weight_history "
        "WHERE edge_id = $1 AND content_timestamp = $2", edge, MAR)
    assert (entry["weight_band"], entry["previous_weight_band"]) == ("strong", None)


@pytest.mark.asyncio
async def test_newer_weight_update_changes_current(pool) -> None:
    a = await _create(pool, _condition(), JAN)
    b = await _create(pool, _condition(), JAN)
    edge = await _edge(pool, a, b, MAR, band="provisional")
    await _weight(pool, edge, "moderate", OCT)

    assert await pool.fetchval(
        "SELECT current_weight_band FROM edges WHERE edge_id = $1", edge) == "moderate"
    entry = await pool.fetchrow(
        "SELECT previous_weight_band FROM edge_weight_history "
        "WHERE edge_id = $1 AND content_timestamp = $2", edge, OCT)
    assert entry["previous_weight_band"] == "provisional"


@pytest.mark.asyncio
async def test_older_supporting_signal_reaches_edge_back(pool) -> None:
    a = await _create(pool, _condition(), JAN)
    b = await _create(pool, _condition(), JAN)
    edge = await _edge(pool, a, b, OCT)
    op = AddSupportingSignalOperation(target_edge_id=str(edge), signal_id=str(uuid.uuid4()))
    await Applier(pool.pool).apply([op], content_timestamp=MAR, source="test")

    row = await pool.fetchrow("SELECT created_at, updated_at FROM edges WHERE edge_id = $1", edge)
    assert (row["created_at"], row["updated_at"]) == (MAR, OCT)
