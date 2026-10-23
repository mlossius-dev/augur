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
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from augur.graph.alias_resolver import AliasResolver
from augur.graph.models import (
    MergeNodesOperation,
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
    ClaimAssessment,
    ConditionState,
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
REJECT_MERGE_SAME_NODE = "survivor and absorbed are the same node"
REJECT_MERGE_ALREADY_MERGED = "node is already merged into another"
REJECT_MERGE_TYPE_CHANGE = (
    "different node types merge only with allow_type_change, and only when the "
    "absorbed node has no activation or assessment history"
)

# Strongest first: which of two duplicate edges a merge keeps
_BAND_RANK = {"strong": 4, "moderate": 3, "weak": 2, "provisional": 1, "disputed": 0}

# Fields stored in enum-typed history columns: an unknown value would fail the
# whole batch's transaction in Postgres, so the Applier rejects the operation.
_ENUM_FIELDS = {
    "current_state": ConditionState,
    "current_state_confidence": WeightBand,
    "current_assessment": ClaimAssessment,
}


def invalid_enum_field(fields: dict[str, Any]) -> str | None:
    """A rejection reason if a state/assessment field holds an unknown value."""
    for name, enum in _ENUM_FIELDS.items():
        value = fields.get(name)
        if value is not None and value not in set(enum):
            allowed = ", ".join(e.value for e in enum)
            return f"{name} {value!r} is not one of: {allowed}"
    return None


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
        llm_call_ids: list[str] | None = None,
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
            llm_call_ids: llm_calls IDs of the LLM calls behind this batch.

        Returns:
            ApplierResult with applied and rejected events.
        """
        _triggered_by = triggered_by or []
        _call_ids = llm_call_ids or []

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
                        llm_call_ids=_call_ids,
                    )
                    await self._collect(event, result, conn)

                # ── Step 2: update nodes ──────────────────────────────────────
                for op in update_nodes:
                    event = await self._apply_update_node(
                        op, conn, id_map,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                    )
                    await self._collect(event, result, conn)

                # ── Step 3: create edges ──────────────────────────────────────
                for op in create_edges:
                    event = await self._apply_create_edge(
                        op, conn, id_map,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                        llm_call_ids=_call_ids,
                    )
                    await self._collect(event, result, conn)

                # ── Step 4: update edge weights ───────────────────────────────
                for op in update_weights:
                    event = await self._apply_update_edge_weight(
                        op, conn,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                    )
                    await self._collect(event, result, conn)

                # ── Step 5: add supporting / disconfirming signals ────────────
                for op in add_supporting:
                    event = await self._apply_add_signal(
                        op, conn, supporting=True,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                    )
                    await self._collect(event, result, conn)

                for op in add_disconfirming:
                    event = await self._apply_add_signal(
                        op, conn, supporting=False,
                        content_timestamp=content_timestamp,
                        source=source,
                        triggered_by=_triggered_by,
                    )
                    await self._collect(event, result, conn)

        log.info(
            "applier.batch_done",
            applied=len(result.applied),
            rejected=len(result.rejected),
        )
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _collect(
        self, event: GraphUpdateEvent, result: ApplierResult, conn: asyncpg.Connection
    ) -> None:
        """
        Sort an event into applied or rejected.  Applied mutations record
        themselves; rejections and alias matches are recorded here, so the
        event log holds every operation the Applier was given, and anchoring
        quality (rejection rates) can be read from it.
        """
        if event.rejected:
            result.rejected.append(event)
            await self._record_event(event, conn)
        else:
            result.applied.append(event)
            if event.event_type == "create_node_aliased":
                await self._record_event(event, conn)

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
            # A merged-away node resolves to its survivor
            return await self._follow_merges(conn, node_id) if exists else None
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

    # ── Time order (docs/augur-graph-schema.md, "Time order versus processing
    # order"): "current" is the latest by content_timestamp, never the latest
    # processed.  An operation older than what its target already reflects is
    # recorded in history only, and existence reaches back to the earliest
    # evidence.

    @staticmethod
    async def _history_position(
        conn: asyncpg.Connection,
        *,
        table: str,
        key_col: str,
        value_col: str,
        key: UUID,
        content_timestamp: datetime,
    ) -> tuple[bool, Any]:
        """
        (stale, value_in_force) for an append to a history table.

        stale: an entry later than `content_timestamp` already exists, so the
        new entry must not change the current value.  value_in_force: the
        latest value at or before `content_timestamp` (None if none).
        """
        latest = await conn.fetchval(
            f"SELECT max(content_timestamp) FROM {table} WHERE {key_col} = $1", key
        )
        in_force = await conn.fetchval(
            f"SELECT {value_col} FROM {table} "
            f"WHERE {key_col} = $1 AND content_timestamp <= $2 "
            "ORDER BY content_timestamp DESC LIMIT 1",
            key,
            content_timestamp,
        )
        return (latest is not None and content_timestamp < latest), in_force

    @staticmethod
    async def _touch_nodes(
        conn: asyncpg.Connection, node_ids: list[UUID], content_timestamp: datetime
    ) -> None:
        """Evidence at content_timestamp: reach created_at back, never move updated_at back."""
        await conn.execute(
            "UPDATE nodes SET created_at = LEAST(created_at, $2), "
            "updated_at = GREATEST(updated_at, $2) WHERE node_id = ANY($1)",
            node_ids,
            content_timestamp,
        )

    @staticmethod
    async def _follow_merges(conn: asyncpg.Connection, node_id: UUID) -> UUID:
        """The node a (possibly merged-away) node now lives in."""
        for _ in range(10):  # merges chain at most a few deep
            survivor = await conn.fetchval(
                "SELECT merged_into FROM nodes WHERE node_id = $1", node_id
            )
            if not isinstance(survivor, UUID):
                return node_id
            node_id = survivor
        return node_id

    # ── merge_nodes (operator-only) ───────────────────────────────────────────

    async def merge_nodes(
        self, op: MergeNodesOperation, *, source: str = "operator"
    ) -> GraphUpdateEvent:
        """
        Merge `op.absorbed_id` into `op.survivor_id` in one transaction.

        docs/augur-graph-schema.md, "Merging duplicate nodes": edges, history,
        references, aliases and provenance move to the survivor; duplicate
        edges collapse without reweighting; the absorbed node stays as a
        tombstone.  The event carries the processing time: a merge is an
        identity correction, not something that happened in the world.
        """
        now = datetime.now(timezone.utc)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                event = await self._merge(op, conn, now=now, source=source)
                await self._record_event(event, conn)
        log.info("applier.merge_nodes", survivor=op.survivor_id, absorbed=op.absorbed_id,
                 rejected=event.rejected, reason=event.rejection_reason)
        return event

    async def _merge(
        self, op: MergeNodesOperation, conn: asyncpg.Connection, *, now: datetime, source: str
    ) -> GraphUpdateEvent:
        try:
            survivor_id, absorbed_id = UUID(op.survivor_id), UUID(op.absorbed_id)
        except ValueError:
            return self._reject(op, REJECT_UNRESOLVED_TARGET, now, source, [])
        if survivor_id == absorbed_id:
            return self._reject(op, REJECT_MERGE_SAME_NODE, now, source, [])

        rows = {
            r["node_id"]: r
            for r in await conn.fetch(
                "SELECT * FROM nodes WHERE node_id = ANY($1) FOR UPDATE",
                [survivor_id, absorbed_id],
            )
        }
        if survivor_id not in rows or absorbed_id not in rows:
            return self._reject(op, REJECT_UNRESOLVED_TARGET, now, source, [])
        survivor, absorbed = rows[survivor_id], rows[absorbed_id]
        if survivor["merged_into"] is not None or absorbed["merged_into"] is not None:
            return self._reject(op, REJECT_MERGE_ALREADY_MERGED, now, source, [])

        if survivor["node_type"] != absorbed["node_type"]:
            history = await conn.fetchval(
                "SELECT (SELECT count(*) FROM condition_state_history WHERE node_id = $1)"
                " + (SELECT count(*) FROM claim_assessment_history WHERE node_id = $1)",
                absorbed_id,
            )
            if not op.allow_type_change or history:
                return self._reject(op, REJECT_MERGE_TYPE_CHANGE, now, source, [])

        moved = await self._merge_edges(conn, survivor_id, absorbed_id)
        history = await self._merge_history(conn, survivor_id, absorbed_id, survivor["node_type"])
        references = await self._merge_references(conn, survivor, absorbed)

        # Provenance: union; existence from the earlier, activity to the later
        await conn.execute(
            """
            UPDATE nodes s SET
                created_from = ARRAY(SELECT DISTINCT unnest(s.created_from || a.created_from)),
                llm_call_ids = ARRAY(SELECT DISTINCT unnest(s.llm_call_ids || a.llm_call_ids)),
                created_at   = LEAST(s.created_at, a.created_at),
                updated_at   = GREATEST(s.updated_at, a.updated_at)
            FROM nodes a
            WHERE s.node_id = $1 AND a.node_id = $2
            """,
            survivor_id,
            absorbed_id,
        )
        # Tombstone: kept, never deleted
        await conn.execute(
            "UPDATE nodes SET merged_into = $2, merged_at = $3 WHERE node_id = $1",
            absorbed_id,
            survivor_id,
            now,
        )
        await self._age_merge_vertex(conn, absorbed_id, moved["kept"])

        snapshot = {
            "absorbed": {k: absorbed[k] for k in
                         ("node_id", "node_type", "name", "description", "type_data",
                          "created_from", "created_at", "updated_at")},
            "edges_repointed": [str(e) for e in moved["repointed"]],
            "edges_deprecated": [str(e) for e in moved["deprecated"]],
            "history_moved": history,
            **references,
        }
        return GraphUpdateEvent(
            event_type="merge_nodes",
            target_node_id=survivor_id,
            operation_data={**op.model_dump(), "snapshot": snapshot},
            triggered_by=[],
            reasoning=op.reasoning,
            content_timestamp=now,
            source=source,
        )

    async def _merge_edges(
        self, conn: asyncpg.Connection, survivor_id: UUID, absorbed_id: UUID
    ) -> dict[str, list[UUID]]:
        """Re-point the absorbed node's edges; deprecate self-loops and duplicates."""
        repointed = [
            r["edge_id"] for r in await conn.fetch(
                """
                UPDATE edges SET
                    source_node_id = CASE WHEN source_node_id = $2 THEN $1 ELSE source_node_id END,
                    target_node_id = CASE WHEN target_node_id = $2 THEN $1 ELSE target_node_id END
                WHERE source_node_id = $2 OR target_node_id = $2
                RETURNING edge_id
                """,
                survivor_id,
                absorbed_id,
            )
        ]
        deprecated: list[UUID] = []
        # Self-loops: the two nodes were linked to each other
        deprecated += [
            r["edge_id"] for r in await conn.fetch(
                "UPDATE edges SET deprecated = TRUE, deprecated_at = now() "
                "WHERE source_node_id = $1 AND target_node_id = $1 AND NOT deprecated "
                "RETURNING edge_id",
                survivor_id,
            )
        ]
        # Duplicates: same source, target and type now; keep the strongest
        groups = await conn.fetch(
            """
            SELECT array_agg(edge_id) AS ids, array_agg(current_weight_band::text) AS bands,
                   array_agg(created_at) AS created
            FROM edges
            WHERE (source_node_id = $1 OR target_node_id = $1) AND NOT deprecated
            GROUP BY source_node_id, target_node_id, edge_type
            HAVING count(*) > 1
            """,
            survivor_id,
        )
        for g in groups:
            members = sorted(
                zip(g["ids"], g["bands"], g["created"]),
                key=lambda m: (-_BAND_RANK.get(m[1], 0), m[2]),
            )
            keep, drop = members[0][0], [m[0] for m in members[1:]]
            await conn.execute(
                """
                UPDATE edges k SET
                    supporting_signals    = ARRAY(SELECT DISTINCT unnest(k.supporting_signals
                                              || (SELECT coalesce(array_agg(x), '{}') FROM edges d,
                                                  unnest(d.supporting_signals) x WHERE d.edge_id = ANY($2)))),
                    disconfirming_signals = ARRAY(SELECT DISTINCT unnest(k.disconfirming_signals
                                              || (SELECT coalesce(array_agg(x), '{}') FROM edges d,
                                                  unnest(d.disconfirming_signals) x WHERE d.edge_id = ANY($2)))),
                    created_from          = ARRAY(SELECT DISTINCT unnest(k.created_from
                                              || (SELECT coalesce(array_agg(x), '{}') FROM edges d,
                                                  unnest(d.created_from) x WHERE d.edge_id = ANY($2)))),
                    created_at            = LEAST(k.created_at,
                                              (SELECT min(created_at) FROM edges WHERE edge_id = ANY($2)))
                WHERE k.edge_id = $1
                """,
                keep,
                drop,
            )
            await conn.execute(
                "UPDATE edges SET deprecated = TRUE, deprecated_at = now() WHERE edge_id = ANY($1)",
                drop,
            )
            deprecated += drop
        kept = [e for e in repointed if e not in set(deprecated)]
        return {"repointed": repointed, "deprecated": deprecated, "kept": kept}

    async def _merge_history(
        self, conn: asyncpg.Connection, survivor_id: UUID, absorbed_id: UUID, survivor_type: str
    ) -> dict[str, int]:
        """Re-point state histories; the survivor's state is the latest by content time."""
        moved: dict[str, int] = {}
        for table, value_col, field in (
            ("condition_state_history", "new_state", "current_state"),
            ("claim_assessment_history", "new_assessment", "current_assessment"),
        ):
            status = await conn.execute(
                f"UPDATE {table} SET node_id = $1 WHERE node_id = $2", survivor_id, absorbed_id
            )
            moved[table] = int(status.split()[-1])
            if moved[table] and survivor_type in ("condition", "claim"):
                latest = await conn.fetchval(
                    f"SELECT {value_col}::text FROM {table} WHERE node_id = $1 "
                    "ORDER BY content_timestamp DESC LIMIT 1",
                    survivor_id,
                )
                if latest is not None:
                    await conn.execute(
                        "UPDATE nodes SET type_data = type_data || jsonb_build_object($2::text, $3::text) "
                        "WHERE node_id = $1",
                        survivor_id,
                        field,
                        latest,
                    )
        return moved

    async def _merge_references(
        self, conn: asyncpg.Connection, survivor: Any, absorbed: Any
    ) -> dict[str, Any]:
        """Topics, scenarios and aliases follow the survivor."""
        sid, aid = survivor["node_id"], absorbed["node_id"]
        topics = [
            r["topic_id"] for r in await conn.fetch(
                "SELECT topic_id FROM topic_nodes WHERE node_id = $1", aid)
        ]
        await conn.execute(
            """
            INSERT INTO topic_nodes (topic_id, node_id, notes, added_by)
            SELECT topic_id, $1, notes, added_by FROM topic_nodes WHERE node_id = $2
            ON CONFLICT DO NOTHING
            """,
            sid,
            aid,
        )
        await conn.execute("DELETE FROM topic_nodes WHERE node_id = $1", aid)
        scenarios = await conn.execute(
            "UPDATE scenarios SET key_condition_ids = array_replace(key_condition_ids, $2, $1) "
            "WHERE $2 = ANY(key_condition_ids)",
            sid,
            aid,
        )
        # The absorbed node's aliases, and its own name, now resolve to the survivor
        await conn.execute(
            "UPDATE aliases SET canonical_node_id = $1, canonical_name = $2 "
            "WHERE canonical_node_id = $3",
            sid,
            survivor["name"],
            aid,
        )
        await conn.execute(
            """
            INSERT INTO aliases (alias_text, canonical_node_id, canonical_name, added_by)
            VALUES ($1, $2, $3, 'operator')
            ON CONFLICT (alias_text) DO NOTHING
            """,
            absorbed["name"],
            sid,
            survivor["name"],
        )
        return {
            "topics": [str(t) for t in topics],
            "scenarios_updated": int(scenarios.split()[-1]),
        }

    async def _age_merge_vertex(
        self, conn: asyncpg.Connection, absorbed_id: UUID, kept_edges: list[UUID]
    ) -> None:
        """Mirror the merge in AGE: drop the absorbed vertex, re-create its moved edges."""
        cypher = (
            f"SELECT * FROM cypher('{AGE_GRAPH_NAME}', $$"
            f" MATCH (a {{node_id: '{absorbed_id}'}}) DETACH DELETE a"
            f"$$) AS (v agtype)"
        )
        try:
            async with conn.transaction():  # savepoint, as in _age_create_vertex
                await conn.execute("LOAD 'age'")
                await conn.execute("SET search_path = ag_catalog, \"$user\", public")
                await conn.execute(cypher)
        except Exception as exc:
            log.error("applier.age_merge_failed", node_id=str(absorbed_id), error=str(exc))
        for row in await conn.fetch(
            "SELECT edge_id, source_node_id, target_node_id, edge_type FROM edges "
            "WHERE edge_id = ANY($1)",
            kept_edges,
        ):
            await self._age_create_edge(
                conn, row["edge_id"], row["source_node_id"], row["target_node_id"],
                EdgeType(row["edge_type"]),
            )

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
        llm_call_ids: list[str],
    ) -> GraphUpdateEvent:
        # Validate node_type
        if op.node_type not in set(NodeType):
            return self._reject(op, REJECT_UNKNOWN_NODE_TYPE, content_timestamp, source, triggered_by)

        # Required: name in fields
        name = op.fields.get("name", "").strip()
        if not name:
            return self._reject(op, REJECT_MISSING_NODE_NAME, content_timestamp, source, triggered_by)

        # Per-type required field validation, then enum-typed values
        reject_reason = self._validate_node_fields(op.node_type, op.fields) or invalid_enum_field(
            op.fields
        )
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
                await self._touch_nodes(conn, [resolved.canonical_node_id], content_timestamp)
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
                 created_from, llm_call_ids, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
            """,
            node_id,
            str(op.node_type),
            name,
            description,
            json.dumps(self._extract_type_data(op.node_type, op.fields)),
            triggered_by,
            llm_call_ids,
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
        reject_reason = invalid_enum_field(updates or {})
        if reject_reason:
            return self._reject(op, reject_reason, content_timestamp, source, triggered_by)
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

        # A field changes its current value only if this operation is not older
        # than what the node already reflects: history fields decide by their
        # own history, the rest by updated_at.
        node_updated_at = await conn.fetchval(
            "SELECT updated_at FROM nodes WHERE node_id = $1", node_id
        )
        node_stale = node_updated_at is not None and content_timestamp < node_updated_at

        state_stale, state_in_force = False, None
        if "current_state" in updates:
            state_stale, state_in_force = await self._history_position(
                conn, table="condition_state_history", key_col="node_id",
                value_col="new_state", key=node_id, content_timestamp=content_timestamp,
            )
        assessment_stale, assessment_in_force = False, None
        if "current_assessment" in updates:
            assessment_stale, assessment_in_force = await self._history_position(
                conn, table="claim_assessment_history", key_col="node_id",
                value_col="new_assessment", key=node_id, content_timestamp=content_timestamp,
            )

        # Collect changes to the main columns vs type_data
        top_level_updates: dict[str, Any] = {}
        type_data_updates: dict[str, Any] = {}

        for k, v in updates.items():
            if k == "current_state":
                stale = state_stale
            elif k == "current_assessment":
                stale = assessment_stale
            else:
                stale = node_stale
            if stale:
                continue  # history fields still enter history below
            if k in ("name", "description"):
                top_level_updates[k] = v
            else:
                type_data_updates[k] = v

        if top_level_updates:
            set_clauses = ", ".join(
                f"{col} = ${i + 2}" for i, col in enumerate(top_level_updates)
            )
            await conn.execute(
                f"UPDATE nodes SET {set_clauses} WHERE node_id = $1",
                node_id,
                *top_level_updates.values(),
            )

        if type_data_updates:
            await conn.execute(
                "UPDATE nodes SET type_data = type_data || $2 WHERE node_id = $1",
                node_id,
                json.dumps(type_data_updates),
            )

        await self._touch_nodes(conn, [node_id], content_timestamp)

        # State changes always enter history at their own time; the previous
        # value is the one in force at that time, read before anything changed.
        if "current_state" in updates:
            await conn.execute(
                """
                INSERT INTO condition_state_history
                    (node_id, new_state, previous_state, confidence_band,
                     reasoning, triggered_by, content_timestamp)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                node_id,
                updates["current_state"],
                state_in_force,
                updates.get("current_state_confidence"),
                op.reasoning,
                triggered_by,
                content_timestamp,
            )

        if "current_assessment" in updates:
            await conn.execute(
                """
                INSERT INTO claim_assessment_history
                    (node_id, new_assessment, previous_assessment,
                     reasoning, triggered_by, content_timestamp)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                node_id,
                updates["current_assessment"],
                assessment_in_force,
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
        llm_call_ids: list[str],
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

        # An edge at this time is evidence both endpoints existed then
        await self._touch_nodes(conn, [src_id, tgt_id], content_timestamp)

        edge_id = uuid.uuid4()

        await conn.execute(
            """
            INSERT INTO edges
                (edge_id, source_node_id, target_node_id, edge_type,
                 current_weight_band, reasoning, falsification_criteria,
                 created_from, llm_call_ids, created_at, updated_at)
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
            llm_call_ids,
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

        change_type = "strengthened" if op.direction == "strengthen" else "weakened"
        if source == "disconfirmation":
            change_type = "disconfirmation"

        # The band changes only if no later history entry exists; either way
        # the entry goes into history at its own time, against the band in
        # force then.
        stale, band_in_force = await self._history_position(
            conn, table="edge_weight_history", key_col="edge_id",
            value_col="weight_band", key=edge_id, content_timestamp=content_timestamp,
        )
        previous_band = band_in_force  # None if nothing was in force yet
        if not stale:
            await conn.execute(
                "UPDATE edges SET current_weight_band = $2 WHERE edge_id = $1",
                edge_id,
                str(op.new_weight_band),
            )
        await conn.execute(
            "UPDATE edges SET created_at = LEAST(created_at, $2), "
            "updated_at = GREATEST(updated_at, $2) WHERE edge_id = $1",
            edge_id,
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
            f"UPDATE edges SET {col} = array_append({col}, $2), "
            "created_at = LEAST(created_at, $3), updated_at = GREATEST(updated_at, $3) "
            "WHERE edge_id = $1",
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
            # Savepoint: a failed AGE statement aborts the *current* transaction
            # in Postgres. Without isolating it, the caught exception would leave
            # the batch transaction poisoned and the very next write (e.g. the
            # graph_update_events insert) would fail. The nested transaction rolls
            # back only the AGE attempt, keeping the Postgres write intact.
            async with conn.transaction():
                await conn.execute("LOAD 'age'")
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
            # Savepoint — see _age_create_vertex: isolate AGE failure so it
            # cannot poison the batch transaction.
            async with conn.transaction():
                await conn.execute("LOAD 'age'")
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
