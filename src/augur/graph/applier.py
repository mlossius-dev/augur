"""
The Augur Applier.

The gate between proposed graph mutations and Tier B.  This module is
entirely plain Python — no LLM calls, no heuristics, no inference.

Architecture invariant (docs/augur-architecture.md):
    'No LLM ever writes directly to the graph.  The applier is plain Python
     code.  LLMs propose structured anchors; the applier validates and applies
     them.'

Responsibilities:
1. Validate schema: node/edge types in the allowed set, required fields
   present (especially falsification_criteria on create_edge), weight bands
   within the named set.
2. Resolve aliases: normalise entity names against the alias table; rewrite
   create_node → update_node when a match is found.
3. Resolve forward references: map proposed_id slugs from the same batch to
   actual UUIDs, in topological order.
4. Check references: every target_node_id and target_edge_id must exist in
   the DB (or be a resolved forward reference from the same batch).
5. Enforce invariants: no orphan edges, max anchors per signal, etc.
6. Write: apply valid operations to Postgres (nodes, edges, history tables)
   and mirror to the AGE graph.
7. Record: write an immutable GraphUpdateEvent for every operation, both
   applied and rejected.

The public API is a single async method:

    result = await applier.apply(
        operations,
        content_timestamp=datetime(...),
        source="anchoring",
        triggered_by=[signal_id, ...],
    )
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from augur.graph.alias_resolver import AliasResolver
from augur.graph.models import (
    AddDisconfirmingSignalOperation,
    AddSupportingSignalOperation,
    ApplierResult,
    CreateEdgeOperation,
    CreateNodeOperation,
    GraphUpdateEvent,
    UpdateEdgeWeightOperation,
    UpdateNodeOperation,
)
from augur.graph.schema import (
    AGE_GRAPH_NAME,
    AGE_NODE_LABELS,
    EdgeType,
    NodeType,
    WeightBand,
)

log = structlog.get_logger(__name__)


# ── Rejection reasons ─────────────────────────────────────────────────────────
# String constants so tests can assert on exact messages.

REJECT_UNKNOWN_NODE_TYPE = "unknown node_type"
REJECT_UNKNOWN_EDGE_TYPE = "unknown edge_type"
REJECT_UNKNOWN_WEIGHT_BAND = "unknown weight_band"
REJECT_MISSING_FALSIFICATION = "falsification_criteria is required on create_edge"
REJECT_EMPTY_FALSIFICATION = "falsification_criteria must not be empty"
REJECT_UNRESOLVED_SOURCE = "source_node_id could not be resolved"
REJECT_UNRESOLVED_TARGET = "target_node_id could not be resolved"
REJECT_UNRESOLVED_EDGE = "target_edge_id could not be resolved"
REJECT_SELF_LOOP = "source_node_id and target_node_id are the same"
REJECT_MISSING_NODE_NAME = "create_node requires a non-empty name in fields"
REJECT_MISSING_ENTITY_KIND = "Entity node requires entity_kind in fields"
REJECT_MISSING_OCCURRED_AT = "Event node requires occurred_at in fields"
REJECT_MISSING_EVENT_KIND = "Event node requires event_kind in fields"
REJECT_MISSING_UNIT = "Quantity node requires unit in fields"
REJECT_MISSING_CLAIM_TEXT = "Claim node requires claim_text in fields"
REJECT_MISSING_CLAIM_KIND = "Claim node requires claim_kind in fields"
REJECT_DEPRECATED_EDGE = "target edge is deprecated"


class Applier:
    """
    Validates and applies a batch of proposed anchor operations to Tier B.

    Instantiate once and reuse; the pool is shared.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._alias_resolver = AliasResolver(pool)

    async def apply(
        self,
        operations: list[Any],
        *,
        content_timestamp: datetime,
        source: str = "anchoring",
        triggered_by: list[UUID] | None = None,
        langfuse_trace_ids: list[str] | None = None,
    ) -> ApplierResult:
        """
        Apply a batch of proposed anchor operations.

        Args:
            operations: List of ProposedAnchorOperation (already parsed by Pydantic).
            content_timestamp: The time the originating signal represents.
                               Used on all created nodes, edges, and history records.
                               This is the replay-mode anchor — NOT now().
            source: One of anchoring | disconfirmation | operator_override | seed.
            triggered_by: Signal IDs that drove these operations.
            langfuse_trace_ids: Langfuse trace IDs from the calling LLM stage.

        Returns:
            ApplierResult with applied and rejected events.
        """
        _triggered_by = triggered_by or []
        _trace_ids = langfuse_trace_ids or []

        log.info(
            "applier.batch_start",
            n_operations=len(operations),
            source=source,
            content_timestamp=content_timestamp.isoformat(),
        )

        # Phase 1: separate operations by type; create_nodes first (forward refs)
        create_nodes = [op for op in operations if isinstance(op, CreateNodeOperation)]
        create_edges = [op for op in operations if isinstance(op, CreateEdgeOperation)]
        update_nodes = [op for op in operations if isinstance(op, UpdateNodeOperation)]
        update_weights = [op for op in operations if isinstance(op, UpdateEdgeWeightOperation)]
        add_supporting = [op for op in operations if isinstance(op, AddSupportingSignalOperation)]
        add_disconfirming = [op for op in operations if isinstance(op, AddDisconfirmingSignalOperation)]

        result = ApplierResult()

        # Maps proposed_id slug → resolved UUID within this batch
        id_map: dict[str, UUID] = {}

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # ── Step 1: create nodes ──────────────────────────────────────
                for op in create_nodes:
                    event = await self._apply_create_node(
                        op, conn, id_map,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                        langfuse_trace_ids=_trace_ids,
                    )
                    (result.applied if not event.rejected else result.rejected).append(event)

                # ── Step 2: update nodes ──────────────────────────────────────
                for op in update_nodes:
                    event = await self._apply_update_node(
                        op, conn, id_map,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                    )
                    (result.applied if not event.rejected else result.rejected).append(event)

                # ── Step 3: create edges ──────────────────────────────────────
                for op in create_edges:
                    event = await self._apply_create_edge(
                        op, conn, id_map,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                        langfuse_trace_ids=_trace_ids,
                    )
                    (result.applied if not event.rejected else result.rejected).append(event)

                # ── Step 4: update edge weights ───────────────────────────────
                for op in update_weights:
                    event = await self._apply_update_edge_weight(
                        op, conn,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                    )
                    (result.applied if not event.rejected else result.rejected).append(event)

                # ── Step 5: add supporting / disconfirming signals ────────────
                for op in add_supporting:
                    event = await self._apply_add_signal(
                        op, conn, supporting=True,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                    )
                    (result.applied if not event.rejected else result.rejected).append(event)

                for op in add_disconfirming:
                    event = await self._apply_add_signal(
                        op, conn, supporting=False,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                    )
                    (result.applied if not event.rejected else result.rejected).append(event)

        log.info(
            "applier.batch_done",
            applied=len(result.applied),
            rejected=len(result.rejected),
        )
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _reject(
        self,
        op: Any,
        reason: str,
        content_timestamp: datetime,
        source: str,
        triggered_by: list[UUID],
    ) -> GraphUpdateEvent:
        log.warning("applier.rejected", operation=op.operation, reason=reason)
        return GraphUpdateEvent(
            event_type=op.operation,
            operation_data=op.model_dump(),
            triggered_by=triggered_by,
            reasoning=reason,
            content_timestamp=content_timestamp,
            source=source,
            rejected=True,
            rejection_reason=reason,
        )

    async def _resolve_node_id(
        self, ref: str, id_map: dict[str, UUID], conn: asyncpg.Connection
    ) -> UUID | None:
        """
        Resolve a node reference to a UUID.

        `ref` is either:
        - A UUID4 string  → look up in the DB; return None if not found.
        - A slug          → look up in the batch's id_map (forward reference).
        """
        try:
            node_id = UUID(ref)
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM nodes WHERE node_id = $1)", node_id
            )
            return node_id if exists else None
        except ValueError:
            # slug: look up in batch id_map
            return id_map.get(ref)

    async def _resolve_edge_id(
        self, ref: str, conn: asyncpg.Connection
    ) -> UUID | None:
        try:
            edge_id = UUID(ref)
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM edges WHERE edge_id = $1)", edge_id
            )
            return edge_id if exists else None
        except ValueError:
            return None

    # ── create_node ───────────────────────────────────────────────────────────

    async def _apply_create_node(
        self,
        op: CreateNodeOperation,
        conn: asyncpg.Connection,
        id_map: dict[str, UUID],
        *,
        content_timestamp: datetime,
        source: str,
        triggered_by: list[UUID],
        langfuse_trace_ids: list[str],
    ) -> GraphUpdateEvent:
        # Validate node_type
        if op.node_type not in set(NodeType):
            return self._reject(op, REJECT_UNKNOWN_NODE_TYPE, content_timestamp, source, triggered_by)

        # Required: name in fields
        name = op.fields.get("name", "").strip()
        if not name:
            return self._reject(op, REJECT_MISSING_NODE_NAME, content_timestamp, source, triggered_by)

        # Per-type required field validation
        reject_reason = self._validate_node_fields(op.node_type, op.fields)
        if reject_reason:
            return self._reject(op, reject_reason, content_timestamp, source, triggered_by)

        # Alias resolution for Entity nodes
        if op.node_type == NodeType.ENTITY:
            resolved = await self._alias_resolver.resolve(name)
            if resolved:
                # Rewrite: entity already exists — update its supporting signals instead
                log.info(
                    "applier.alias_rewrite",
                    proposed=name,
                    canonical=resolved.canonical_name,
                    node_id=str(resolved.canonical_node_id),
                )
                id_map[op.proposed_id] = resolved.canonical_node_id
                # Record as an applied event pointing to the existing node
                return GraphUpdateEvent(
                    event_type="create_node_aliased",
                    target_node_id=resolved.canonical_node_id,
                    operation_data={**op.model_dump(), "_resolved_to": str(resolved.canonical_node_id)},
                    triggered_by=triggered_by,
                    reasoning=f"Alias match: '{name}' → '{resolved.canonical_name}'",
                    content_timestamp=content_timestamp,
                    source=source,
                )

        # Create the node in Postgres
        node_id = uuid.uuid4()
        description = op.fields.get("description")

        await conn.execute(
            """
            INSERT INTO nodes
                (node_id, node_type, name, description, type_data,
                 created_from, langfuse_trace_ids, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
            """,
            node_id,
            str(op.node_type),
            name,
            description,
            json.dumps(self._extract_type_data(op.node_type, op.fields)),
            triggered_by,
            langfuse_trace_ids,
            content_timestamp,
        )

        # Register the proposed_id → actual UUID mapping for this batch
        id_map[op.proposed_id] = node_id

        # For Entity nodes: register the name and aliases in the alias table
        if op.node_type == NodeType.ENTITY:
            await self._alias_resolver.register(
                alias_text=name,
                canonical_name=name,
                canonical_node_id=node_id,
                added_by=source,
                conn=conn,
            )
            # Also register any explicit aliases listed in fields
            for alias in op.fields.get("aliases", []):
                await self._alias_resolver.register(
                    alias_text=alias,
                    canonical_name=name,
                    canonical_node_id=node_id,
                    added_by=source,
                    conn=conn,
                )
            # Attach node_id to any pre-seed aliases for this name
            await self._alias_resolver.attach_node_id(
                canonical_name=name,
                canonical_node_id=node_id,
                conn=conn,
            )

        # For Condition nodes: write initial state to history
        if op.node_type == NodeType.CONDITION:
            initial_state = op.fields.get("current_state", "unknown")
            await conn.execute(
                """
                INSERT INTO condition_state_history
                    (node_id, new_state, previous_state, confidence_band,
                     reasoning, triggered_by, content_timestamp)
                VALUES ($1, $2, NULL, $3, $4, $5, $6)
                """,
                node_id,
                initial_state,
                op.fields.get("current_state_confidence"),
                op.reasoning,
                triggered_by,
                content_timestamp,
            )

        # For Claim nodes: write initial assessment to history
        if op.node_type == NodeType.CLAIM:
            initial_assessment = op.fields.get("current_assessment", "weakly_supported")
            await conn.execute(
                """
                INSERT INTO claim_assessment_history
                    (node_id, new_assessment, previous_assessment,
                     reasoning, triggered_by, content_timestamp)
                VALUES ($1, $2, NULL, $3, $4, $5)
                """,
                node_id,
                initial_assessment,
                op.reasoning,
                triggered_by,
                content_timestamp,
            )

        # Mirror to AGE graph
        await self._age_create_vertex(conn, node_id, op.node_type, name)

        event = GraphUpdateEvent(
            event_type="create_node",
            target_node_id=node_id,
            operation_data=op.model_dump(),
            triggered_by=triggered_by,
            reasoning=op.reasoning,
            content_timestamp=content_timestamp,
            source=source,
        )
        await self._record_event(event, conn)
        return event

    # ── update_node ───────────────────────────────────────────────────────────

    async def _apply_update_node(
        self,
        op: UpdateNodeOperation,
        conn: asyncpg.Connection,
        id_map: dict[str, UUID],
        *,
        content_timestamp: datetime,
        source: str,
        triggered_by: list[UUID],
    ) -> GraphUpdateEvent:
        node_id = await self._resolve_node_id(op.target_node_id, id_map, conn)
        if node_id is None:
            return self._reject(op, REJECT_UNRESOLVED_TARGET, content_timestamp, source, triggered_by)

        updates = op.field_updates
        if not updates:
            # Nothing to do — not a rejection but also not interesting
            return GraphUpdateEvent(
                event_type="update_node_noop",
                target_node_id=node_id,
                operation_data=op.model_dump(),
                triggered_by=triggered_by,
                reasoning="no field_updates provided",
                content_timestamp=content_timestamp,
                source=source,
            )

        # Collect changes to the main columns vs type_data
        top_level_updates: dict[str, Any] = {}
        type_data_updates: dict[str, Any] = {}

        for k, v in updates.items():
            if k in ("name", "description"):
                top_level_updates[k] = v
            else:
                type_data_updates[k] = v

        if top_level_updates:
            set_clauses = ", ".join(
                f"{col} = ${i + 2}" for i, col in enumerate(top_level_updates)
            )
            await conn.execute(
                f"UPDATE nodes SET {set_clauses}, updated_at = ${len(top_level_updates) + 2} "
                f"WHERE node_id = $1",
                node_id,
                *top_level_updates.values(),
                content_timestamp,
            )

        if type_data_updates:
            await conn.execute(
                "UPDATE nodes SET type_data = type_data || $2, updated_at = $3 WHERE node_id = $1",
                node_id,
                json.dumps(type_data_updates),
                content_timestamp,
            )

        # If condition state changed, write a history record
        if "current_state" in updates:
            # Fetch previous state
            prev = await conn.fetchval(
                "SELECT type_data->>'current_state' FROM nodes WHERE node_id = $1",
                node_id,
            )
            await conn.execute(
                """
                INSERT INTO condition_state_history
                    (node_id, new_state, previous_state, confidence_band,
                     reasoning, triggered_by, content_timestamp)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                node_id,
                updates["current_state"],
                prev,
                updates.get("current_state_confidence"),
                op.reasoning,
                triggered_by,
                content_timestamp,
            )

        # If claim assessment changed, write a history record
        if "current_assessment" in updates:
            prev = await conn.fetchval(
                "SELECT type_data->>'current_assessment' FROM nodes WHERE node_id = $1",
                node_id,
            )
            await conn.execute(
                """
                INSERT INTO claim_assessment_history
                    (node_id, new_assessment, previous_assessment,
                     reasoning, triggered_by, content_timestamp)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                node_id,
                updates["current_assessment"],
                prev,
                op.reasoning,
                triggered_by,
                content_timestamp,
            )

        event = GraphUpdateEvent(
            event_type="update_node",
            target_node_id=node_id,
            operation_data=op.model_dump(),
            triggered_by=triggered_by,
            reasoning=op.reasoning,
            content_timestamp=content_timestamp,
            source=source,
        )
        await self._record_event(event, conn)
        return event

    # ── create_edge ───────────────────────────────────────────────────────────

    async def _apply_create_edge(
        self,
        op: CreateEdgeOperation,
        conn: asyncpg.Connection,
        id_map: dict[str, UUID],
        *,
        content_timestamp: datetime,
        source: str,
        triggered_by: list[UUID],
        langfuse_trace_ids: list[str],
    ) -> GraphUpdateEvent:
        # Validate edge_type
        if op.edge_type not in set(EdgeType):
            return self._reject(op, REJECT_UNKNOWN_EDGE_TYPE, content_timestamp, source, triggered_by)

        # Validate weight_band
        if op.proposed_weight_band not in set(WeightBand):
            return self._reject(op, REJECT_UNKNOWN_WEIGHT_BAND, content_timestamp, source, triggered_by)

        # falsification_criteria is enforced by Pydantic, but double-check here too
        if not op.falsification_criteria or not op.falsification_criteria.strip():
            return self._reject(op, REJECT_MISSING_FALSIFICATION, content_timestamp, source, triggered_by)

        # Resolve source and target node IDs
        src_id = await self._resolve_node_id(op.source_node_id, id_map, conn)
        if src_id is None:
            return self._reject(op, REJECT_UNRESOLVED_SOURCE, content_timestamp, source, triggered_by)

        tgt_id = await self._resolve_node_id(op.target_node_id, id_map, conn)
        if tgt_id is None:
            return self._reject(op, REJECT_UNRESOLVED_TARGET, content_timestamp, source, triggered_by)

        # No self-loops
        if src_id == tgt_id:
            return self._reject(op, REJECT_SELF_LOOP, content_timestamp, source, triggered_by)

        edge_id = uuid.uuid4()

        await conn.execute(
            """
            INSERT INTO edges
                (edge_id, source_node_id, target_node_id, edge_type,
                 current_weight_band, reasoning, falsification_criteria,
                 created_from, langfuse_trace_ids, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10)
            """,
            edge_id,
            src_id,
            tgt_id,
            str(op.edge_type),
            str(op.proposed_weight_band),
            op.reasoning,
            op.falsification_criteria,
            triggered_by,
            langfuse_trace_ids,
            content_timestamp,
        )

        # Write initial weight history entry
        await conn.execute(
            """
            INSERT INTO edge_weight_history
                (edge_id, weight_band, previous_weight_band, change_type,
                 reasoning, triggered_by, content_timestamp)
            VALUES ($1, $2, NULL, 'initial', $3, $4, $5)
            """,
            edge_id,
            str(op.proposed_weight_band),
            op.reasoning,
            triggered_by,
            content_timestamp,
        )

        # Mirror to AGE graph
        await self._age_create_edge(conn, edge_id, src_id, tgt_id, op.edge_type)

        event = GraphUpdateEvent(
            event_type="create_edge",
            target_edge_id=edge_id,
            operation_data=op.model_dump(),
            triggered_by=triggered_by,
            reasoning=op.reasoning,
            content_timestamp=content_timestamp,
            source=source,
        )
        await self._record_event(event, conn)
        return event

    # ── update_edge_weight ────────────────────────────────────────────────────

    async def _apply_update_edge_weight(
        self,
        op: UpdateEdgeWeightOperation,
        conn: asyncpg.Connection,
        *,
        content_timestamp: datetime,
        source: str,
        triggered_by: list[UUID],
    ) -> GraphUpdateEvent:
        if op.new_weight_band not in set(WeightBand):
            return self._reject(op, REJECT_UNKNOWN_WEIGHT_BAND, content_timestamp, source, triggered_by)

        edge_id = await self._resolve_edge_id(op.target_edge_id, conn)
        if edge_id is None:
            return self._reject(op, REJECT_UNRESOLVED_EDGE, content_timestamp, source, triggered_by)

        # Fetch current state
        row = await conn.fetchrow(
            "SELECT current_weight_band, deprecated FROM edges WHERE edge_id = $1",
            edge_id,
        )
        if row["deprecated"]:
            return self._reject(op, REJECT_DEPRECATED_EDGE, content_timestamp, source, triggered_by)

        previous_band = row["current_weight_band"]
        change_type = "strengthened" if op.direction == "strengthen" else "weakened"
        if source == "disconfirmation":
            change_type = "disconfirmation"

        # Update the edge
        await conn.execute(
            """
            UPDATE edges
            SET current_weight_band = $2, updated_at = $3
            WHERE edge_id = $1
            """,
            edge_id,
            str(op.new_weight_band),
            content_timestamp,
        )

        # Append to weight history
        await conn.execute(
            """
            INSERT INTO edge_weight_history
                (edge_id, weight_band, previous_weight_band, change_type,
                 reasoning, triggered_by, content_timestamp)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            edge_id,
            str(op.new_weight_band),
            previous_band,
            change_type,
            op.reasoning,
            triggered_by,
            content_timestamp,
        )

        event = GraphUpdateEvent(
            event_type="update_edge_weight",
            target_edge_id=edge_id,
            operation_data=op.model_dump(),
            triggered_by=triggered_by,
            reasoning=op.reasoning,
            content_timestamp=content_timestamp,
            source=source,
        )
        await self._record_event(event, conn)
        return event

    # ── add_supporting / add_disconfirming signal ─────────────────────────────

    async def _apply_add_signal(
        self,
        op: AddSupportingSignalOperation | AddDisconfirmingSignalOperation,
        conn: asyncpg.Connection,
        *,
        supporting: bool,
        content_timestamp: datetime,
        source: str,
        triggered_by: list[UUID],
    ) -> GraphUpdateEvent:
        edge_id = await self._resolve_edge_id(op.target_edge_id, conn)
        if edge_id is None:
            return self._reject(op, REJECT_UNRESOLVED_EDGE, content_timestamp, source, triggered_by)

        try:
            signal_uuid = UUID(op.signal_id)
        except ValueError:
            return self._reject(
                op, f"signal_id is not a valid UUID: {op.signal_id!r}",
                content_timestamp, source, triggered_by,
            )

        col = "supporting_signals" if supporting else "disconfirming_signals"
        await conn.execute(
            f"UPDATE edges SET {col} = array_append({col}, $2), updated_at = $3 WHERE edge_id = $1",
            edge_id,
            signal_uuid,
            content_timestamp,
        )

        event_type = "add_supporting_signal" if supporting else "add_disconfirming_signal"
        event = GraphUpdateEvent(
            event_type=event_type,
            target_edge_id=edge_id,
            operation_data=op.model_dump(),
            triggered_by=triggered_by,
            content_timestamp=content_timestamp,
            source=source,
        )
        await self._record_event(event, conn)
        return event

    # ── Immutable event recorder ──────────────────────────────────────────────

    async def _record_event(
        self, event: GraphUpdateEvent, conn: asyncpg.Connection
    ) -> None:
        await conn.execute(
            """
            INSERT INTO graph_update_events
                (event_id, event_type, target_node_id, target_edge_id,
                 operation_data, triggered_by, reasoning, confidence,
                 content_timestamp, source, rejected, rejection_reason)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
            event.event_id,
            event.event_type,
            event.target_node_id,
            event.target_edge_id,
            json.dumps(event.operation_data, default=str),
            event.triggered_by,
            event.reasoning,
            event.confidence,
            event.content_timestamp,
            event.source,
            event.rejected,
            event.rejection_reason,
        )

    # ── AGE graph helpers ─────────────────────────────────────────────────────

    async def _age_create_vertex(
        self,
        conn: asyncpg.Connection,
        node_id: UUID,
        node_type: NodeType,
        name: str,
    ) -> None:
        label = AGE_NODE_LABELS[node_type]
        # Escape single quotes in name for Cypher string literal
        safe_name = name.replace("'", "\\'")
        cypher = (
            f"SELECT * FROM cypher('{AGE_GRAPH_NAME}', $$"
            f" CREATE (:{label} {{node_id: '{node_id}', name: '{safe_name}'}})"
            f"$$) AS (v agtype)"
        )
        try:
            await conn.execute(f"LOAD 'age'")
            await conn.execute("SET search_path = ag_catalog, \"$user\", public")
            await conn.execute(cypher)
        except Exception as exc:
            # AGE write failure is logged but does not roll back the Postgres write.
            # The graph can be re-synced from the Postgres tables; AGE is a mirror.
            log.error("applier.age_vertex_failed", node_id=str(node_id), error=str(exc))

    async def _age_create_edge(
        self,
        conn: asyncpg.Connection,
        edge_id: UUID,
        src_id: UUID,
        tgt_id: UUID,
        edge_type: EdgeType,
    ) -> None:
        label = str(edge_type)
        cypher = (
            f"SELECT * FROM cypher('{AGE_GRAPH_NAME}', $$"
            f" MATCH (s {{node_id: '{src_id}'}}), (t {{node_id: '{tgt_id}'}})"
            f" CREATE (s)-[:{label} {{edge_id: '{edge_id}'}}]->(t)"
            f"$$) AS (e agtype)"
        )
        try:
            await conn.execute(f"LOAD 'age'")
            await conn.execute("SET search_path = ag_catalog, \"$user\", public")
            await conn.execute(cypher)
        except Exception as exc:
            log.error("applier.age_edge_failed", edge_id=str(edge_id), error=str(exc))

    # ── Field validation helpers ──────────────────────────────────────────────

    @staticmethod
    def _validate_node_fields(node_type: NodeType, fields: dict[str, Any]) -> str | None:
        """Return a rejection reason string if required fields are missing; else None."""
        if node_type == NodeType.ENTITY:
            if not fields.get("entity_kind"):
                return REJECT_MISSING_ENTITY_KIND
        elif node_type == NodeType.EVENT:
            if not fields.get("occurred_at"):
                return REJECT_MISSING_OCCURRED_AT
            if not fields.get("event_kind"):
                return REJECT_MISSING_EVENT_KIND
        elif node_type == NodeType.QUANTITY:
            if not fields.get("unit"):
                return REJECT_MISSING_UNIT
        elif node_type == NodeType.CLAIM:
            if not fields.get("claim_text", "").strip():
                return REJECT_MISSING_CLAIM_TEXT
            if not fields.get("claim_kind"):
                return REJECT_MISSING_CLAIM_KIND
        return None

    @staticmethod
    def _extract_type_data(node_type: NodeType, fields: dict[str, Any]) -> dict[str, Any]:
        """
        Return the type_data dict to store in the DB.
        Strips keys that are stored as top-level columns (name, description).
        """
        exclude = {"name", "description"}
        return {k: v for k, v in fields.items() if k not in exclude}
