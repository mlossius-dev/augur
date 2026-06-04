"""
Graph reader: read-only queries against Tier B.

All public methods accept an optional `as_of` datetime parameter.
When provided, results reflect the graph state at that timestamp —
this is the replay-mode read path that mirrors the replay-mode write
path in the Applier.

The reader never writes to the DB; it is safe to call from any context.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from augur.graph.models import Edge, Node
from augur.graph.schema import EdgeType, NodeType, WeightBand

log = structlog.get_logger(__name__)


class GraphReader:
    """
    Read-only access to the Tier B graph state.

    Constructed with an asyncpg pool; every method acquires a connection
    from the pool for the duration of the query.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ── Node queries ──────────────────────────────────────────────────────────

    async def get_node(self, node_id: UUID, *, as_of: datetime | None = None) -> Node | None:
        """
        Fetch a single node by ID.

        If `as_of` is provided and the node was created after that timestamp,
        returns None (the node did not exist at that time).
        """
        async with self._pool.acquire() as conn:
            if as_of is None:
                row = await conn.fetchrow(
                    "SELECT * FROM nodes WHERE node_id = $1", node_id
                )
            else:
                row = await conn.fetchrow(
                    "SELECT * FROM nodes WHERE node_id = $1 AND created_at <= $2",
                    node_id, as_of,
                )

        if row is None:
            return None
        return self._row_to_node(row)

    async def get_nodes_by_type(
        self,
        node_type: NodeType,
        *,
        as_of: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Node]:
        """Return all nodes of a given type, optionally filtered by as_of timestamp."""
        async with self._pool.acquire() as conn:
            if as_of is None:
                rows = await conn.fetch(
                    "SELECT * FROM nodes WHERE node_type = $1 ORDER BY created_at DESC LIMIT $2 OFFSET $3",
                    str(node_type), limit, offset,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM nodes WHERE node_type = $1 AND created_at <= $2 "
                    "ORDER BY created_at DESC LIMIT $3 OFFSET $4",
                    str(node_type), as_of, limit, offset,
                )
        return [self._row_to_node(r) for r in rows]

    async def search_nodes(
        self,
        query: str,
        *,
        node_type: NodeType | None = None,
        as_of: datetime | None = None,
        limit: int = 20,
    ) -> list[Node]:
        """
        Trigram similarity search over node names.

        Returns nodes whose name has pg_trgm similarity > 0.1 to `query`,
        ordered by similarity descending.
        """
        params: list[Any] = [query, limit]
        conditions = ["similarity(name, $1) > 0.1"]

        if node_type is not None:
            conditions.append(f"node_type = ${len(params) + 1}")
            params.append(str(node_type))

        if as_of is not None:
            conditions.append(f"created_at <= ${len(params) + 1}")
            params.append(as_of)

        where = " AND ".join(conditions)
        sql = (
            f"SELECT *, similarity(name, $1) AS sim FROM nodes "
            f"WHERE {where} ORDER BY sim DESC LIMIT $2"
        )

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [self._row_to_node(r) for r in rows]

    # ── Edge queries ──────────────────────────────────────────────────────────

    async def get_edge(self, edge_id: UUID, *, as_of: datetime | None = None) -> Edge | None:
        """
        Fetch a single edge by ID.

        If `as_of` is given, the returned edge's `current_weight_band` reflects
        the weight at that timestamp, not the current weight.
        """
        async with self._pool.acquire() as conn:
            if as_of is None:
                row = await conn.fetchrow(
                    "SELECT * FROM edges WHERE edge_id = $1 AND NOT deprecated",
                    edge_id,
                )
            else:
                row = await conn.fetchrow(
                    "SELECT * FROM edges WHERE edge_id = $1 AND created_at <= $2",
                    edge_id, as_of,
                )

        if row is None:
            return None

        edge = self._row_to_edge(row)
        if as_of is not None:
            edge = await self._apply_weight_at(edge, as_of)
        return edge

    async def get_edges_for_node(
        self,
        node_id: UUID,
        *,
        direction: str = "both",
        edge_type: EdgeType | None = None,
        as_of: datetime | None = None,
    ) -> list[Edge]:
        """
        Return edges connected to a node.

        Args:
            direction: "outbound", "inbound", or "both"
            edge_type: Filter to a specific edge type.
            as_of: Return graph state at this timestamp.
        """
        conditions: list[str] = []
        params: list[Any] = [node_id]

        if direction == "outbound":
            conditions.append("source_node_id = $1")
        elif direction == "inbound":
            conditions.append("target_node_id = $1")
        else:
            conditions.append("(source_node_id = $1 OR target_node_id = $1)")

        if edge_type is not None:
            conditions.append(f"edge_type = ${len(params) + 1}")
            params.append(str(edge_type))

        if as_of is None:
            conditions.append("NOT deprecated")
        else:
            conditions.append(f"created_at <= ${len(params) + 1}")
            params.append(as_of)

        where = " AND ".join(conditions)
        sql = f"SELECT * FROM edges WHERE {where} ORDER BY created_at DESC"

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        edges = [self._row_to_edge(r) for r in rows]
        if as_of is not None:
            edges = [await self._apply_weight_at(e, as_of) for e in edges]
        return edges

    async def get_edge_weight_at(
        self, edge_id: UUID, as_of: datetime
    ) -> WeightBand | None:
        """
        Return the weight band that was current for `edge_id` at `as_of`.

        Reads the edge_weight_history append-only table and returns the most
        recent entry whose content_timestamp <= as_of.  Returns None if the
        edge did not exist at that time.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT weight_band FROM edge_weight_history
                WHERE edge_id = $1 AND content_timestamp <= $2
                ORDER BY content_timestamp DESC, id DESC
                LIMIT 1
                """,
                edge_id, as_of,
            )
        if row is None:
            return None
        return WeightBand(row["weight_band"])

    # ── Subgraph query ────────────────────────────────────────────────────────

    async def get_subgraph(
        self,
        root_node_id: UUID,
        *,
        depth: int = 2,
        as_of: datetime | None = None,
    ) -> dict[str, Any]:
        """
        Return a subgraph centred on `root_node_id` up to `depth` hops away.

        Returns a dict with keys "nodes" and "edges" containing lists of
        Node and Edge objects respectively.  Suitable for frontend rendering.
        """
        visited_nodes: set[UUID] = set()
        collected_edges: list[Edge] = []
        frontier: set[UUID] = {root_node_id}

        for _ in range(depth):
            if not frontier:
                break
            next_frontier: set[UUID] = set()
            for nid in frontier:
                if nid in visited_nodes:
                    continue
                visited_nodes.add(nid)
                edges = await self.get_edges_for_node(nid, direction="both", as_of=as_of)
                for edge in edges:
                    if edge.edge_id not in {e.edge_id for e in collected_edges}:
                        collected_edges.append(edge)
                    next_frontier.add(edge.source_node_id)
                    next_frontier.add(edge.target_node_id)
            frontier = next_frontier - visited_nodes

        # Fetch all node objects
        nodes: list[Node] = []
        async with self._pool.acquire() as conn:
            for nid in visited_nodes:
                row = await conn.fetchrow(
                    "SELECT * FROM nodes WHERE node_id = $1", nid
                )
                if row:
                    nodes.append(self._row_to_node(row))

        return {"nodes": nodes, "edges": collected_edges}

    # ── History queries ───────────────────────────────────────────────────────

    async def get_condition_history(
        self, node_id: UUID, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return condition state change history for a node, newest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT new_state, previous_state, confidence_band, reasoning,
                       content_timestamp, recorded_at
                FROM condition_state_history
                WHERE node_id = $1
                ORDER BY content_timestamp DESC, id DESC
                LIMIT $2
                """,
                node_id, limit,
            )
        return [dict(r) for r in rows]

    async def get_edge_weight_history(
        self, edge_id: UUID, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return weight change history for an edge, newest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT weight_band, previous_weight_band, change_type, reasoning,
                       content_timestamp, recorded_at
                FROM edge_weight_history
                WHERE edge_id = $1
                ORDER BY content_timestamp DESC, id DESC
                LIMIT $2
                """,
                edge_id, limit,
            )
        return [dict(r) for r in rows]

    # ── Row conversion helpers ────────────────────────────────────────────────

    @staticmethod
    def _row_to_node(row: asyncpg.Record) -> Node:
        type_data = row["type_data"]
        if isinstance(type_data, str):
            type_data = json.loads(type_data)

        created_from = list(row["created_from"]) if row["created_from"] else []
        langfuse_trace_ids = list(row["langfuse_trace_ids"]) if row["langfuse_trace_ids"] else []

        return Node(
            node_id=row["node_id"],
            node_type=NodeType(row["node_type"]),
            name=row["name"],
            description=row["description"],
            type_data=type_data,
            created_from=created_from,
            langfuse_trace_ids=langfuse_trace_ids,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_edge(row: asyncpg.Record) -> Edge:
        supporting = list(row["supporting_signals"]) if row["supporting_signals"] else []
        disconfirming = list(row["disconfirming_signals"]) if row["disconfirming_signals"] else []
        created_from = list(row["created_from"]) if row["created_from"] else []
        langfuse_trace_ids = list(row["langfuse_trace_ids"]) if row["langfuse_trace_ids"] else []

        return Edge(
            edge_id=row["edge_id"],
            source_node_id=row["source_node_id"],
            target_node_id=row["target_node_id"],
            edge_type=EdgeType(row["edge_type"]),
            current_weight_band=WeightBand(row["current_weight_band"]),
            supporting_signals=supporting,
            disconfirming_signals=disconfirming,
            reasoning=row["reasoning"],
            falsification_criteria=row["falsification_criteria"],
            last_disconfirmation_pass=row.get("last_disconfirmation_pass"),
            created_from=created_from,
            langfuse_trace_ids=langfuse_trace_ids,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deprecated=row["deprecated"],
            deprecated_at=row.get("deprecated_at"),
        )

    async def _apply_weight_at(self, edge: Edge, as_of: datetime) -> Edge:
        """Return a copy of `edge` with current_weight_band set to its value at `as_of`."""
        weight = await self.get_edge_weight_at(edge.edge_id, as_of)
        if weight is None:
            return edge
        # Edge is frozen — use model_copy to produce a new instance
        return edge.model_copy(update={"current_weight_band": weight})
