"""
Exhaustive unit tests for the Augur Applier.

The Applier is the only write gate for Tier B; its correctness is critical.
All tests use a mock asyncpg pool and connection so they run without a real DB.

Test groups:
  1. Field validation (create_node): missing / invalid required fields per node type
  2. Alias resolution: entity rewrites on name match
  3. Forward reference resolution: proposed_id slugs resolved within a batch
  4. Edge validation: edge_type, weight_band, falsification_criteria, self-loops
  5. Node reference resolution (create_edge): both UUID strings and slugs
  6. update_node: field updates, condition/claim history side-effects
  7. update_edge_weight: weight band changes, history records, deprecated edge rejection
  8. add_supporting_signal / add_disconfirming_signal: valid and invalid references
  9. Rejection accounting: ApplierResult totals and rates
 10. Batch ordering: create_nodes before create_edges (forward reference)
 11. Replay-mode invariant: content_timestamp propagated to all writes
 12. AGE mirror failures: logged but do not abort the Postgres write
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from augur.graph.applier import (
    REJECT_DEPRECATED_EDGE,
    REJECT_EMPTY_FALSIFICATION,
    REJECT_MISSING_CLAIM_KIND,
    REJECT_MISSING_CLAIM_TEXT,
    REJECT_MISSING_ENTITY_KIND,
    REJECT_MISSING_EVENT_KIND,
    REJECT_MISSING_FALSIFICATION,
    REJECT_MISSING_NODE_NAME,
    REJECT_MISSING_OCCURRED_AT,
    REJECT_MISSING_UNIT,
    REJECT_SELF_LOOP,
    REJECT_UNKNOWN_EDGE_TYPE,
    REJECT_UNKNOWN_NODE_TYPE,
    REJECT_UNKNOWN_WEIGHT_BAND,
    REJECT_UNRESOLVED_EDGE,
    REJECT_UNRESOLVED_SOURCE,
    REJECT_UNRESOLVED_TARGET,
    Applier,
)
from augur.graph.models import (
    AddDisconfirmingSignalOperation,
    AddSupportingSignalOperation,
    ApplierResult,
    CreateEdgeOperation,
    CreateNodeOperation,
    UpdateEdgeWeightOperation,
    UpdateNodeOperation,
)

# ── Shared fixtures ───────────────────────────────────────────────────────────

_TS = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
_NODE_UUID = uuid.uuid4()
_EDGE_UUID = uuid.uuid4()
_SIGNAL_UUID = uuid.uuid4()


def _make_pool(conn: AsyncMock) -> MagicMock:
    """Wrap a mock connection in a mock pool whose acquire() is a context manager."""
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_conn(
    *,
    node_exists: bool = False,
    edge_row: dict[str, Any] | None = None,
    edge_exists: bool = False,
    alias_row=None,
) -> AsyncMock:
    """
    Build a mock asyncpg connection pre-configured with common return values.

    - fetchrow: returns alias_row for alias lookup; edge_row for edge queries
    - fetchval: returns node_exists / edge_exists for existence checks
    - execute: no-op
    - fetch: returns [] (fuzzy candidates)
    - transaction: returns an async context manager (no-op)
    """
    conn = AsyncMock()

    # transaction() must return an async context manager synchronously
    # (the applier calls `async with conn.transaction():`)
    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=None)
    txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=txn)

    # fetchrow: alias resolver uses it; edge queries use it
    conn.fetchrow = AsyncMock(return_value=alias_row)

    # fetchval: node / edge existence checks
    conn.fetchval = AsyncMock(return_value=node_exists)

    # fetch: fuzzy candidates — not called on hot path
    conn.fetch = AsyncMock(return_value=[])

    # execute: write operations (INSERT / UPDATE)
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    if edge_row is not None:
        # When both alias lookup and edge lookup happen, fetchrow returns edge_row
        # for the second call (edge lookup).  We use side_effect for that.
        conn.fetchrow = AsyncMock(side_effect=[alias_row, edge_row])

    return conn


def _make_applier(conn: AsyncMock) -> Applier:
    pool = _make_pool(conn)
    return Applier(pool)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _create_entity(proposed_id: str = "ent", name: str = "Russia") -> CreateNodeOperation:
    return CreateNodeOperation(
        node_type="entity",
        proposed_id=proposed_id,
        fields={"name": name, "entity_kind": "state"},
        reasoning="test",
    )


def _create_condition(proposed_id: str = "cond") -> CreateNodeOperation:
    return CreateNodeOperation(
        node_type="condition",
        proposed_id=proposed_id,
        fields={"name": "War", "current_state": "active"},
        reasoning="test",
    )


def _create_event(proposed_id: str = "evt") -> CreateNodeOperation:
    return CreateNodeOperation(
        node_type="event",
        proposed_id=proposed_id,
        fields={
            "name": "Invasion",
            "event_kind": "geopolitical",
            "occurred_at": "2022-02-24T00:00:00Z",
        },
        reasoning="test",
    )


def _create_quantity(proposed_id: str = "qty") -> CreateNodeOperation:
    return CreateNodeOperation(
        node_type="quantity",
        proposed_id=proposed_id,
        fields={"name": "Gas Price", "unit": "USD/MMBtu"},
        reasoning="test",
    )


def _create_claim(proposed_id: str = "clm") -> CreateNodeOperation:
    return CreateNodeOperation(
        node_type="claim",
        proposed_id=proposed_id,
        fields={
            "name": "Claim: gas weaponised",
            "claim_text": "Russia weaponised gas exports.",
            "claim_kind": "factual",
        },
        reasoning="test",
    )


def _create_scenario(proposed_id: str = "scen") -> CreateNodeOperation:
    return CreateNodeOperation(
        node_type="scenario",
        proposed_id=proposed_id,
        fields={"name": "Scenario A", "projected_trajectory": "Escalation"},
        reasoning="test",
    )


def _edge(src: str, tgt: str, edge_type: str = "causes") -> CreateEdgeOperation:
    return CreateEdgeOperation(
        source_node_id=src,
        target_node_id=tgt,
        edge_type=edge_type,
        proposed_weight_band="moderate",
        reasoning="test",
        falsification_criteria="FC: condition X is not met.",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Field validation — create_node
# ═══════════════════════════════════════════════════════════════════════════════


class TestCreateNodeFieldValidation:
    @pytest.mark.asyncio
    async def test_unknown_node_type_rejected(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        # Use model_construct to bypass Pydantic validation and inject an invalid node_type
        op = CreateNodeOperation.model_construct(
            operation="create_node",
            node_type="spaceship",  # not in NodeType
            proposed_id="x",
            fields={"name": "X", "entity_kind": "state"},
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_UNKNOWN_NODE_TYPE

    @pytest.mark.asyncio
    async def test_missing_name_rejected(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        op = CreateNodeOperation(
            node_type="entity",
            proposed_id="x",
            fields={"entity_kind": "state"},  # no name
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_MISSING_NODE_NAME

    @pytest.mark.asyncio
    async def test_empty_name_rejected(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        op = CreateNodeOperation(
            node_type="entity",
            proposed_id="x",
            fields={"name": "   ", "entity_kind": "state"},
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_MISSING_NODE_NAME

    @pytest.mark.asyncio
    async def test_entity_missing_entity_kind(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        op = CreateNodeOperation(
            node_type="entity",
            proposed_id="x",
            fields={"name": "Russia"},  # no entity_kind
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_MISSING_ENTITY_KIND

    @pytest.mark.asyncio
    async def test_event_missing_occurred_at(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        op = CreateNodeOperation(
            node_type="event",
            proposed_id="x",
            fields={"name": "War", "event_kind": "geopolitical"},
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_MISSING_OCCURRED_AT

    @pytest.mark.asyncio
    async def test_event_missing_event_kind(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        op = CreateNodeOperation(
            node_type="event",
            proposed_id="x",
            fields={"name": "War", "occurred_at": "2022-02-24T00:00:00Z"},
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_MISSING_EVENT_KIND

    @pytest.mark.asyncio
    async def test_quantity_missing_unit(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        op = CreateNodeOperation(
            node_type="quantity",
            proposed_id="x",
            fields={"name": "Price"},  # no unit
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_MISSING_UNIT

    @pytest.mark.asyncio
    async def test_claim_missing_claim_text(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        op = CreateNodeOperation(
            node_type="claim",
            proposed_id="x",
            fields={"name": "Claim", "claim_kind": "factual"},
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_MISSING_CLAIM_TEXT

    @pytest.mark.asyncio
    async def test_claim_whitespace_claim_text(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        op = CreateNodeOperation(
            node_type="claim",
            proposed_id="x",
            fields={"name": "Claim", "claim_text": "   ", "claim_kind": "factual"},
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_MISSING_CLAIM_TEXT

    @pytest.mark.asyncio
    async def test_claim_missing_claim_kind(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        op = CreateNodeOperation(
            node_type="claim",
            proposed_id="x",
            fields={"name": "Claim", "claim_text": "Russia weaponised gas."},
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_MISSING_CLAIM_KIND

    @pytest.mark.asyncio
    async def test_valid_entity_applied(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        result = await applier.apply([_create_entity()], content_timestamp=_TS)
        assert len(result.applied) == 1
        assert len(result.rejected) == 0
        assert result.applied[0].event_type == "create_node"

    @pytest.mark.asyncio
    async def test_valid_condition_applied(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        result = await applier.apply([_create_condition()], content_timestamp=_TS)
        assert len(result.applied) == 1

    @pytest.mark.asyncio
    async def test_valid_event_applied(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        result = await applier.apply([_create_event()], content_timestamp=_TS)
        assert len(result.applied) == 1

    @pytest.mark.asyncio
    async def test_valid_quantity_applied(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        result = await applier.apply([_create_quantity()], content_timestamp=_TS)
        assert len(result.applied) == 1

    @pytest.mark.asyncio
    async def test_valid_claim_applied(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        result = await applier.apply([_create_claim()], content_timestamp=_TS)
        assert len(result.applied) == 1

    @pytest.mark.asyncio
    async def test_valid_scenario_applied(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        result = await applier.apply([_create_scenario()], content_timestamp=_TS)
        assert len(result.applied) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Alias resolution — entity rewrites
# ═══════════════════════════════════════════════════════════════════════════════


class TestAliasResolution:
    @pytest.mark.asyncio
    async def test_known_alias_produces_alias_rewrite_event(self):
        existing_node_id = uuid.uuid4()
        alias_row = MagicMock()
        alias_row.__getitem__ = lambda self, k: {
            "canonical_node_id": str(existing_node_id),
            "canonical_name": "Russia",
            "alias_text": "Russia",
        }[k]

        conn = _make_conn(alias_row=alias_row)
        applier = _make_applier(conn)
        op = _create_entity(name="Russia")
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.applied) == 1
        assert result.applied[0].event_type == "create_node_aliased"
        assert result.applied[0].target_node_id == existing_node_id

    @pytest.mark.asyncio
    async def test_alias_rewrite_maps_proposed_id(self):
        """Forward-referencing the proposed_id after alias resolution must resolve correctly."""
        existing_node_id = uuid.uuid4()
        alias_row = MagicMock()
        alias_row.__getitem__ = lambda self, k: {
            "canonical_node_id": str(existing_node_id),
            "canonical_name": "Russia",
            "alias_text": "Russia",
        }[k]

        # Make edge resolver find the node
        conn = _make_conn(alias_row=alias_row, node_exists=True)
        applier = _make_applier(conn)
        node_op = _create_entity(proposed_id="russia", name="Russia")
        edge_op = _edge("russia", "russia")  # Will be rejected as self-loop after resolution

        # Both resolve to the same existing_node_id → self-loop rejection
        result = await applier.apply([node_op, edge_op], content_timestamp=_TS)
        # The node op is aliased (applied); the edge op should be rejected (self-loop)
        assert result.applied[0].event_type == "create_node_aliased"
        assert any(e.rejection_reason == REJECT_SELF_LOOP for e in result.rejected)

    @pytest.mark.asyncio
    async def test_no_alias_match_creates_new_node(self):
        conn = _make_conn(alias_row=None)
        applier = _make_applier(conn)
        result = await applier.apply([_create_entity()], content_timestamp=_TS)
        assert len(result.applied) == 1
        assert result.applied[0].event_type == "create_node"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Forward reference resolution
# ═══════════════════════════════════════════════════════════════════════════════


class TestForwardReferenceResolution:
    @pytest.mark.asyncio
    async def test_slug_resolves_to_created_node_uuid(self):
        """An edge using proposed_id slugs from the same batch must be applied."""
        conn = _make_conn(alias_row=None, node_exists=False)
        applier = _make_applier(conn)
        node_a = _create_entity(proposed_id="a", name="Entity A")
        node_b = _create_entity(proposed_id="b", name="Entity B")
        edge = _edge("a", "b")
        result = await applier.apply([node_a, node_b, edge], content_timestamp=_TS)
        assert len(result.rejected) == 0
        assert len(result.applied) == 3

    @pytest.mark.asyncio
    async def test_unknown_slug_rejects_edge(self):
        conn = _make_conn(alias_row=None, node_exists=False)
        applier = _make_applier(conn)
        edge = _edge("nonexistent_slug", "another_missing_slug")
        result = await applier.apply([edge], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_UNRESOLVED_SOURCE

    @pytest.mark.asyncio
    async def test_known_uuid_resolves_when_node_exists(self):
        conn = _make_conn(alias_row=None, node_exists=True)
        applier = _make_applier(conn)
        src = str(uuid.uuid4())
        tgt = str(uuid.uuid4())
        edge = _edge(src, tgt)
        result = await applier.apply([edge], content_timestamp=_TS)
        assert len(result.rejected) == 0
        assert result.applied[0].event_type == "create_edge"

    @pytest.mark.asyncio
    async def test_uuid_ref_to_nonexistent_node_rejected(self):
        conn = _make_conn(alias_row=None, node_exists=False)
        applier = _make_applier(conn)
        src = str(uuid.uuid4())
        tgt = str(uuid.uuid4())
        edge = _edge(src, tgt)
        result = await applier.apply([edge], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert result.rejected[0].rejection_reason == REJECT_UNRESOLVED_SOURCE


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Edge validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeValidation:
    @pytest.mark.asyncio
    async def test_unknown_edge_type_rejected(self):
        conn = _make_conn(node_exists=True)
        applier = _make_applier(conn)
        # Use model_construct to bypass Pydantic validation and inject an invalid edge_type
        op = CreateEdgeOperation.model_construct(
            operation="create_edge",
            edge_type="laser_beam",
            proposed_weight_band="moderate",
            falsification_criteria="FC.",
            source_node_id=str(uuid.uuid4()),
            target_node_id=str(uuid.uuid4()),
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert result.rejected[0].rejection_reason == REJECT_UNKNOWN_EDGE_TYPE

    @pytest.mark.asyncio
    async def test_self_loop_rejected(self):
        conn = _make_conn(node_exists=True)
        applier = _make_applier(conn)
        same_id = str(uuid.uuid4())
        edge = _edge(same_id, same_id)
        result = await applier.apply([edge], content_timestamp=_TS)
        assert result.rejected[0].rejection_reason == REJECT_SELF_LOOP

    @pytest.mark.asyncio
    async def test_empty_falsification_criteria_rejected(self):
        """Pydantic rejects empty FC at parse time — verify the validator fires."""
        with pytest.raises(Exception):
            CreateEdgeOperation(
                source_node_id="a",
                target_node_id="b",
                edge_type="causes",
                proposed_weight_band="moderate",
                reasoning="test",
                falsification_criteria="",
            )

    @pytest.mark.asyncio
    async def test_whitespace_falsification_criteria_rejected(self):
        with pytest.raises(Exception):
            CreateEdgeOperation(
                source_node_id="a",
                target_node_id="b",
                edge_type="causes",
                proposed_weight_band="moderate",
                reasoning="test",
                falsification_criteria="   ",
            )

    @pytest.mark.asyncio
    async def test_all_valid_edge_types_accepted(self):
        """Every EdgeType value must produce an applied event."""
        from augur.graph.schema import EdgeType
        for et in EdgeType:
            conn = _make_conn(node_exists=True)
            applier = _make_applier(conn)
            src, tgt = str(uuid.uuid4()), str(uuid.uuid4())
            op = CreateEdgeOperation(
                source_node_id=src,
                target_node_id=tgt,
                edge_type=et,
                proposed_weight_band="moderate",
                reasoning="test",
                falsification_criteria="FC.",
            )
            result = await applier.apply([op], content_timestamp=_TS)
            assert len(result.rejected) == 0, f"{et} was rejected"

    @pytest.mark.asyncio
    async def test_all_valid_weight_bands_accepted(self):
        from augur.graph.schema import WeightBand
        for wb in WeightBand:
            conn = _make_conn(node_exists=True)
            applier = _make_applier(conn)
            src, tgt = str(uuid.uuid4()), str(uuid.uuid4())
            op = CreateEdgeOperation(
                source_node_id=src,
                target_node_id=tgt,
                edge_type="causes",
                proposed_weight_band=wb,
                reasoning="test",
                falsification_criteria="FC.",
            )
            result = await applier.apply([op], content_timestamp=_TS)
            assert len(result.rejected) == 0, f"{wb} was rejected"


# ═══════════════════════════════════════════════════════════════════════════════
# 5. update_node
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateNode:
    @pytest.mark.asyncio
    async def test_unresolved_target_rejected(self):
        conn = _make_conn(node_exists=False)
        applier = _make_applier(conn)
        op = UpdateNodeOperation(
            target_node_id=str(uuid.uuid4()),
            field_updates={"name": "New Name"},
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert result.rejected[0].rejection_reason == REJECT_UNRESOLVED_TARGET

    @pytest.mark.asyncio
    async def test_empty_field_updates_is_noop(self):
        conn = _make_conn(node_exists=True)
        applier = _make_applier(conn)
        op = UpdateNodeOperation(
            target_node_id=str(uuid.uuid4()),
            field_updates={},
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.applied) == 1
        assert result.applied[0].event_type == "update_node_noop"

    @pytest.mark.asyncio
    async def test_valid_update_writes_event(self):
        conn = _make_conn(node_exists=True)
        applier = _make_applier(conn)
        op = UpdateNodeOperation(
            target_node_id=str(uuid.uuid4()),
            field_updates={"name": "Updated Name"},
            reasoning="update name",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.applied) == 1
        assert result.applied[0].event_type == "update_node"

    @pytest.mark.asyncio
    async def test_condition_state_change_writes_history(self):
        conn = _make_conn(node_exists=True)
        conn.fetchval = AsyncMock(side_effect=[True, "active"])  # exists, prev state
        applier = _make_applier(conn)
        op = UpdateNodeOperation(
            target_node_id=str(uuid.uuid4()),
            field_updates={"current_state": "inactive"},
            reasoning="state changed",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.applied) == 1
        # Verify that an INSERT to condition_state_history was attempted
        calls = [str(c) for c in conn.execute.call_args_list]
        assert any("condition_state_history" in c for c in calls)

    @pytest.mark.asyncio
    async def test_claim_assessment_change_writes_history(self):
        conn = _make_conn(node_exists=True)
        conn.fetchval = AsyncMock(side_effect=[True, "weakly_supported"])
        applier = _make_applier(conn)
        op = UpdateNodeOperation(
            target_node_id=str(uuid.uuid4()),
            field_updates={"current_assessment": "well_supported"},
            reasoning="assessment updated",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.applied) == 1
        calls = [str(c) for c in conn.execute.call_args_list]
        assert any("claim_assessment_history" in c for c in calls)

    @pytest.mark.asyncio
    async def test_slug_target_resolves_from_id_map(self):
        """An update_node can reference a node created in the same batch by proposed_id."""
        conn = _make_conn(alias_row=None, node_exists=False)
        applier = _make_applier(conn)
        create_op = _create_entity(proposed_id="new_ent")
        update_op = UpdateNodeOperation(
            target_node_id="new_ent",
            field_updates={"description": "Updated desc"},
            reasoning="update right after create",
        )
        result = await applier.apply([create_op, update_op], content_timestamp=_TS)
        assert len(result.rejected) == 0
        assert len(result.applied) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 6. update_edge_weight
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpdateEdgeWeight:
    def _edge_row(self, deprecated: bool = False, band: str = "moderate") -> MagicMock:
        row = MagicMock()
        row.__getitem__ = lambda self, k: {"current_weight_band": band, "deprecated": deprecated}[k]
        return row

    @pytest.mark.asyncio
    async def test_unresolved_edge_rejected(self):
        conn = _make_conn(node_exists=False)
        conn.fetchrow = AsyncMock(return_value=None)  # edge not found
        conn.fetchval = AsyncMock(return_value=False)
        applier = _make_applier(conn)
        op = UpdateEdgeWeightOperation(
            target_edge_id=str(uuid.uuid4()),
            new_weight_band="strong",
            direction="strengthen",
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert result.rejected[0].rejection_reason == REJECT_UNRESOLVED_EDGE

    @pytest.mark.asyncio
    async def test_deprecated_edge_rejected(self):
        conn = _make_conn(node_exists=True)
        conn.fetchval = AsyncMock(return_value=True)  # edge_id exists
        conn.fetchrow = AsyncMock(return_value=self._edge_row(deprecated=True))
        applier = _make_applier(conn)
        op = UpdateEdgeWeightOperation(
            target_edge_id=str(uuid.uuid4()),
            new_weight_band="strong",
            direction="strengthen",
            reasoning="test",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert result.rejected[0].rejection_reason == REJECT_DEPRECATED_EDGE

    @pytest.mark.asyncio
    async def test_valid_weight_update_applied(self):
        conn = _make_conn(node_exists=True)
        conn.fetchval = AsyncMock(return_value=True)
        conn.fetchrow = AsyncMock(return_value=self._edge_row())
        conn.execute = AsyncMock(return_value="UPDATE 1")
        applier = _make_applier(conn)
        op = UpdateEdgeWeightOperation(
            target_edge_id=str(uuid.uuid4()),
            new_weight_band="strong",
            direction="strengthen",
            reasoning="new evidence",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.applied) == 1
        assert result.applied[0].event_type == "update_edge_weight"

    @pytest.mark.asyncio
    async def test_disconfirmation_source_sets_change_type(self):
        conn = _make_conn(node_exists=True)
        conn.fetchval = AsyncMock(return_value=True)
        conn.fetchrow = AsyncMock(return_value=self._edge_row())
        conn.execute = AsyncMock(return_value="UPDATE 1")
        applier = _make_applier(conn)
        op = UpdateEdgeWeightOperation(
            target_edge_id=str(uuid.uuid4()),
            new_weight_band="weak",
            direction="weaken",
            reasoning="disconfirming signal",
        )
        result = await applier.apply(
            [op], content_timestamp=_TS, source="disconfirmation"
        )
        assert len(result.applied) == 1
        # The INSERT to edge_weight_history should contain 'disconfirmation'
        calls = [str(c) for c in conn.execute.call_args_list]
        assert any("disconfirmation" in c for c in calls)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. add_supporting_signal / add_disconfirming_signal
# ═══════════════════════════════════════════════════════════════════════════════


class TestAddSignal:
    @pytest.mark.asyncio
    async def test_add_supporting_signal_applied(self):
        conn = _make_conn(node_exists=True)
        conn.fetchval = AsyncMock(return_value=True)
        conn.execute = AsyncMock(return_value="UPDATE 1")
        applier = _make_applier(conn)
        op = AddSupportingSignalOperation(
            target_edge_id=str(uuid.uuid4()),
            signal_id=str(uuid.uuid4()),
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.applied) == 1
        assert result.applied[0].event_type == "add_supporting_signal"

    @pytest.mark.asyncio
    async def test_add_disconfirming_signal_applied(self):
        conn = _make_conn(node_exists=True)
        conn.fetchval = AsyncMock(return_value=True)
        conn.execute = AsyncMock(return_value="UPDATE 1")
        applier = _make_applier(conn)
        op = AddDisconfirmingSignalOperation(
            target_edge_id=str(uuid.uuid4()),
            signal_id=str(uuid.uuid4()),
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.applied) == 1
        assert result.applied[0].event_type == "add_disconfirming_signal"

    @pytest.mark.asyncio
    async def test_invalid_signal_uuid_rejected(self):
        conn = _make_conn(node_exists=True)
        conn.fetchval = AsyncMock(return_value=True)
        applier = _make_applier(conn)
        op = AddSupportingSignalOperation(
            target_edge_id=str(uuid.uuid4()),
            signal_id="not-a-uuid",
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert len(result.rejected) == 1
        assert "not a valid UUID" in result.rejected[0].rejection_reason

    @pytest.mark.asyncio
    async def test_unresolved_edge_id_rejected(self):
        conn = _make_conn(node_exists=True)
        conn.fetchval = AsyncMock(return_value=False)  # edge not found
        applier = _make_applier(conn)
        op = AddSupportingSignalOperation(
            target_edge_id=str(uuid.uuid4()),
            signal_id=str(uuid.uuid4()),
        )
        result = await applier.apply([op], content_timestamp=_TS)
        assert result.rejected[0].rejection_reason == REJECT_UNRESOLVED_EDGE


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Rejection accounting
# ═══════════════════════════════════════════════════════════════════════════════


class TestRejectionAccounting:
    @pytest.mark.asyncio
    async def test_mixed_batch_counts_correct(self):
        conn = _make_conn(alias_row=None, node_exists=False)
        applier = _make_applier(conn)
        good = _create_entity(proposed_id="ok", name="Russia")
        bad = CreateNodeOperation(
            node_type="entity",
            proposed_id="bad",
            fields={"name": "X"},  # missing entity_kind
            reasoning="test",
        )
        result = await applier.apply([good, bad], content_timestamp=_TS)
        assert result.total == 2
        assert len(result.applied) == 1
        assert len(result.rejected) == 1

    @pytest.mark.asyncio
    async def test_rejection_rate_all_rejected(self):
        conn = _make_conn(alias_row=None, node_exists=False)
        applier = _make_applier(conn)
        ops = [
            CreateNodeOperation(
                node_type="entity", proposed_id=f"x{i}",
                fields={"name": "X"},  # missing entity_kind
                reasoning="test",
            )
            for i in range(4)
        ]
        result = await applier.apply(ops, content_timestamp=_TS)
        assert result.rejection_rate == 1.0

    @pytest.mark.asyncio
    async def test_rejection_rate_none_rejected(self):
        conn = _make_conn(alias_row=None, node_exists=False)
        applier = _make_applier(conn)
        ops = [_create_entity(proposed_id=f"e{i}", name=f"Entity{i}") for i in range(3)]
        result = await applier.apply(ops, content_timestamp=_TS)
        assert result.rejection_rate == 0.0

    @pytest.mark.asyncio
    async def test_empty_batch_returns_zero_totals(self):
        conn = _make_conn()
        applier = _make_applier(conn)
        result = await applier.apply([], content_timestamp=_TS)
        assert result.total == 0
        assert result.rejection_rate == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Batch ordering
# ═══════════════════════════════════════════════════════════════════════════════


class TestBatchOrdering:
    @pytest.mark.asyncio
    async def test_create_edges_processed_after_create_nodes(self):
        """
        An edge referencing two nodes created in the same batch must succeed
        even though the batch is submitted as [node_a, edge, node_b].
        The Applier separates create_nodes from create_edges and processes
        nodes first.
        """
        conn = _make_conn(alias_row=None, node_exists=False)
        applier = _make_applier(conn)
        node_a = _create_entity(proposed_id="a", name="Entity A")
        node_b = _create_entity(proposed_id="b", name="Entity B")
        edge = _edge("a", "b")
        # Submit in scrambled order: edge is between the two nodes
        result = await applier.apply([node_a, edge, node_b], content_timestamp=_TS)
        assert len(result.rejected) == 0
        assert len(result.applied) == 3

    @pytest.mark.asyncio
    async def test_update_node_after_create_node_in_same_batch(self):
        conn = _make_conn(alias_row=None, node_exists=False)
        applier = _make_applier(conn)
        create_op = _create_entity(proposed_id="ent", name="Russia")
        update_op = UpdateNodeOperation(
            target_node_id="ent",
            field_updates={"description": "Updated"},
            reasoning="test",
        )
        result = await applier.apply([create_op, update_op], content_timestamp=_TS)
        assert len(result.rejected) == 0
        assert len(result.applied) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Replay-mode: content_timestamp propagated correctly
# ═══════════════════════════════════════════════════════════════════════════════


class TestReplayMode:
    @pytest.mark.asyncio
    async def test_content_timestamp_in_applied_event(self):
        ts = datetime(2022, 3, 1, tzinfo=timezone.utc)
        conn = _make_conn(alias_row=None)
        applier = _make_applier(conn)
        result = await applier.apply([_create_entity()], content_timestamp=ts)
        assert result.applied[0].content_timestamp == ts

    @pytest.mark.asyncio
    async def test_content_timestamp_in_rejected_event(self):
        ts = datetime(2022, 3, 1, tzinfo=timezone.utc)
        conn = _make_conn(alias_row=None)
        applier = _make_applier(conn)
        bad_op = CreateNodeOperation(
            node_type="entity",
            proposed_id="x",
            fields={"name": "X"},  # missing entity_kind
            reasoning="test",
        )
        result = await applier.apply([bad_op], content_timestamp=ts)
        assert result.rejected[0].content_timestamp == ts

    @pytest.mark.asyncio
    async def test_content_timestamp_written_to_db_node(self):
        ts = datetime(2022, 3, 1, tzinfo=timezone.utc)
        conn = _make_conn(alias_row=None)
        applier = _make_applier(conn)
        await applier.apply([_create_entity()], content_timestamp=ts)
        # The INSERT into nodes must include the content_timestamp value
        all_calls = conn.execute.call_args_list
        insert_calls = [c for c in all_calls if "INSERT INTO nodes" in str(c)]
        assert len(insert_calls) >= 1
        # ts must appear somewhere in the call args
        found = any(str(ts) in str(c) or ts in c.args for c in insert_calls)
        assert found

    @pytest.mark.asyncio
    async def test_source_field_propagated(self):
        conn = _make_conn(alias_row=None)
        applier = _make_applier(conn)
        result = await applier.apply([_create_entity()], content_timestamp=_TS, source="seed")
        assert result.applied[0].source == "seed"


# ═══════════════════════════════════════════════════════════════════════════════
# 11. AGE mirror failure is non-fatal
# ═══════════════════════════════════════════════════════════════════════════════


class TestAGEMirrorFailure:
    @pytest.mark.asyncio
    async def test_age_vertex_failure_does_not_roll_back(self):
        """
        If the AGE LOAD command raises an exception, the Postgres write
        should still succeed and produce an applied event.
        """
        conn = _make_conn(alias_row=None)

        # Make execute raise on LOAD 'age' but succeed otherwise
        original_execute = conn.execute

        async def execute_side_effect(sql, *args, **kwargs):
            if "LOAD" in sql or "search_path" in sql or "cypher" in sql.lower():
                raise Exception("AGE not available")
            return await original_execute(sql, *args, **kwargs)

        conn.execute = AsyncMock(side_effect=execute_side_effect)
        applier = _make_applier(conn)
        result = await applier.apply([_create_entity()], content_timestamp=_TS)
        # Despite AGE failure, the operation should be applied (not rejected)
        assert len(result.applied) == 1
        assert result.applied[0].event_type == "create_node"


# ═══════════════════════════════════════════════════════════════════════════════
# 12. _validate_node_fields static method
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidateNodeFieldsStatic:
    """Directly test the static field validation helper."""

    def test_entity_valid(self):
        assert Applier._validate_node_fields("entity", {"name": "X", "entity_kind": "state"}) is None

    def test_entity_invalid(self):
        assert Applier._validate_node_fields("entity", {"name": "X"}) == REJECT_MISSING_ENTITY_KIND

    def test_event_valid(self):
        r = Applier._validate_node_fields(
            "event",
            {"name": "War", "occurred_at": "2022-02-24T00:00:00Z", "event_kind": "geopolitical"},
        )
        assert r is None

    def test_event_missing_occurred_at(self):
        assert Applier._validate_node_fields("event", {"event_kind": "geopolitical"}) == REJECT_MISSING_OCCURRED_AT

    def test_event_missing_event_kind(self):
        assert Applier._validate_node_fields("event", {"occurred_at": "2022-02-24T00:00:00Z"}) == REJECT_MISSING_EVENT_KIND

    def test_quantity_valid(self):
        assert Applier._validate_node_fields("quantity", {"unit": "USD"}) is None

    def test_quantity_missing_unit(self):
        assert Applier._validate_node_fields("quantity", {}) == REJECT_MISSING_UNIT

    def test_claim_valid(self):
        r = Applier._validate_node_fields("claim", {"claim_text": "X caused Y.", "claim_kind": "factual"})
        assert r is None

    def test_claim_missing_text(self):
        assert Applier._validate_node_fields("claim", {"claim_kind": "factual"}) == REJECT_MISSING_CLAIM_TEXT

    def test_claim_missing_kind(self):
        assert Applier._validate_node_fields("claim", {"claim_text": "X."}) == REJECT_MISSING_CLAIM_KIND

    def test_condition_no_required_fields(self):
        assert Applier._validate_node_fields("condition", {}) is None

    def test_scenario_no_required_fields(self):
        assert Applier._validate_node_fields("scenario", {}) is None


# ═══════════════════════════════════════════════════════════════════════════════
# 13. _extract_type_data static method
# ═══════════════════════════════════════════════════════════════════════════════


class TestExtractTypeDataStatic:
    def test_strips_name_and_description(self):
        fields = {"name": "Russia", "description": "A state.", "entity_kind": "state"}
        result = Applier._extract_type_data("entity", fields)
        assert "name" not in result
        assert "description" not in result
        assert result["entity_kind"] == "state"

    def test_preserves_all_other_fields(self):
        fields = {
            "name": "X",
            "current_state": "active",
            "subject_entities": ["abc"],
        }
        result = Applier._extract_type_data("condition", fields)
        assert result == {"current_state": "active", "subject_entities": ["abc"]}
