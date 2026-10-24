"""
Topics from graph clustering (docs/augur-presentation.md, "How topics emerge").

Once a day the live graph is divided into communities (Louvain, seeded);
existing topics follow the community holding most of their members, and
large communities no topic matched become new clustered topics.  Members
the operator added are never removed, hidden topics are never recreated.
Topics are presentation: nothing here writes a node or edge or calls an LLM.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import networkx as nx
import structlog

from augur.presentation.dimensions import DIMENSION_KEYWORDS

log = structlog.get_logger(__name__)

BAND_WEIGHT = {"strong": 3.0, "moderate": 2.0, "weak": 1.0, "provisional": 0.5}
SEED = 7
MATCH_SHARE = 0.30       # share of a topic's members a community must hold to be its
NEW_TOPIC_MIN_NODES = 10
NEW_TOPIC_MIN_ACTIVE = 2

# Keywords as words, so "port" does not match "support" nor "oil" "turmoil"
_DIM_PATTERNS = {dim: [re.compile(r"\b" + re.escape(kw)) for kw in kws]
                 for dim, kws in DIMENSION_KEYWORDS.items()}


@dataclass
class NodeInfo:
    name: str
    node_type: str
    active: bool = False


@dataclass
class TopicState:
    topic_id: str
    name: str
    origin: str
    hidden: bool
    operator_members: set[str] = field(default_factory=set)
    clustered_members: set[str] = field(default_factory=set)

    @property
    def members(self) -> set[str]:
        return self.operator_members | self.clustered_members


@dataclass
class TopicPlan:
    """What one recluster does to one topic."""
    action: str                  # "update" | "create"
    topic_id: str | None
    name: str
    members: set[str]            # clustered members after the run
    added: set[str]
    removed: set[str]
    hubs: list[str]
    dimension: str | None
    active: int
    hidden: bool = False


def build_graph(edges: list[tuple[str, str, str]]) -> nx.Graph:
    """Undirected graph, parallel edges summed by band weight; sorted for a stable division."""
    g = nx.Graph()
    for a, b, band in sorted(edges):
        if a == b:
            continue
        w = BAND_WEIGHT.get(band, 1.0)
        prev = g.get_edge_data(a, b, {"weight": 0.0})["weight"]
        g.add_edge(a, b, weight=prev + w)
    return g


def communities(g: nx.Graph) -> list[set[str]]:
    if g.number_of_edges() == 0:
        return []
    found = nx.community.louvain_communities(g, weight="weight", resolution=1.0, seed=SEED)
    return sorted((set(c) for c in found), key=lambda c: (-len(c), min(c)))


def hubs(g: nx.Graph, community: set[str], n: int = 5) -> list[str]:
    """The community's most connected nodes, by weighted degree inside it."""
    sub = g.subgraph(community)
    return sorted(community, key=lambda x: (-sub.degree(x, weight="weight"), x))[:n]


def match(topics: list[TopicState], comms: list[set[str]]) -> dict[str, int]:
    """Topic → community index, one-to-one, closest fit (Jaccard) first.

    A community is a candidate for a topic when it holds MATCH_SHARE of the
    topic's members. Among candidates the closest overall fit wins, so a small
    topic lying wholly inside a large community does not take it from the
    topic that already is most of it.
    """
    pairs = []
    for t in topics:
        members = t.members
        if not members:
            continue
        for i, c in enumerate(comms):
            overlap = len(members & c)
            if overlap / len(members) >= MATCH_SHARE:
                pairs.append((overlap / len(members | c), overlap, t.topic_id, i))
    pairs.sort(key=lambda p: (-p[0], -p[1], p[2], p[3]))
    out: dict[str, int] = {}
    used: set[int] = set()
    for _, _, tid, i in pairs:
        if tid in out or i in used:
            continue
        out[tid] = i
        used.add(i)
    return out


def _dimension(community: set[str], nodes: dict[str, NodeInfo]) -> str | None:
    """The dimension most of the community's node names point to (one vote each); None if none do."""
    votes: Counter[str] = Counter()
    for x in community:
        if x not in nodes:
            continue
        name = nodes[x].name.lower()
        scores = {dim: sum(1 for kw in pats if kw.search(name)) for dim, pats in _DIM_PATTERNS.items()}
        best = max(scores, key=lambda d: (scores[d], d))
        if scores[best] > 0:
            votes[best] += 1
    return max(votes, key=lambda d: (votes[d], d)) if votes else None


def _name(hub_ids: list[str], nodes: dict[str, NodeInfo]) -> str:
    """Two most connected distinct names, entities and conditions before events."""
    ranked = sorted((h for h in hub_ids if h in nodes),
                    key=lambda h: nodes[h].node_type not in ("entity", "condition"))
    names: list[str] = []
    for h in ranked:
        if nodes[h].name.lower() not in {n.lower() for n in names}:
            names.append(nodes[h].name)
        if len(names) == 2:
            break
    return " · ".join(names)


def plan(
    g: nx.Graph,
    nodes: dict[str, NodeInfo],
    topics: list[TopicState],
) -> list[TopicPlan]:
    comms = communities(g)
    matched = match(topics, comms)
    by_id = {t.topic_id: t for t in topics}
    taken_names = {t.name.lower() for t in topics}
    plans: list[TopicPlan] = []

    for tid, i in sorted(matched.items(), key=lambda kv: kv[1]):
        t, c = by_id[tid], comms[i]
        hub_ids = hubs(g, c)
        new_members = c - t.operator_members
        plans.append(TopicPlan(
            action="update", topic_id=tid, name=t.name, members=new_members,
            added=new_members - t.clustered_members, removed=t.clustered_members - new_members,
            hubs=[nodes[h].name for h in hub_ids if h in nodes],
            dimension=_dimension(c, nodes),
            active=sum(1 for x in c if x in nodes and nodes[x].active),
            hidden=t.hidden,
        ))

    used = set(matched.values())
    for i, c in enumerate(comms):
        if i in used or len(c) < NEW_TOPIC_MIN_NODES:
            continue
        active = sum(1 for x in c if x in nodes and nodes[x].active)
        if active < NEW_TOPIC_MIN_ACTIVE:
            continue
        hub_ids = hubs(g, c, n=10)
        hub_names = [nodes[h].name for h in hub_ids[:5] if h in nodes]
        name = _unique(_name(hub_ids, nodes), taken_names)
        taken_names.add(name.lower())
        plans.append(TopicPlan(
            action="create", topic_id=None, name=name, members=set(c), added=set(c),
            removed=set(), hubs=hub_names, dimension=_dimension(c, nodes), active=active,
        ))
    return plans


def _unique(name: str, taken: set[str]) -> str:
    name = name[:120] or "Unnamed cluster"
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} ({n})".lower() in taken:
        n += 1
    return f"{name} ({n})"


def _description(p: TopicPlan) -> str:
    return "Clustered from the graph around: " + ", ".join(p.hubs) + "."


async def load(conn: asyncpg.Connection) -> tuple[nx.Graph, dict[str, NodeInfo], list[TopicState]]:
    edge_rows = await conn.fetch(
        """
        SELECT e.source_node_id::text AS a, e.target_node_id::text AS b,
               e.current_weight_band::text AS band
        FROM edges e
        JOIN nodes s ON s.node_id = e.source_node_id AND s.merged_into IS NULL
        JOIN nodes t ON t.node_id = e.target_node_id AND t.merged_into IS NULL
        WHERE NOT e.deprecated
        """
    )
    node_rows = await conn.fetch(
        """
        SELECT node_id::text AS id, name, node_type::text AS node_type,
               coalesce(type_data->>'current_state', '') = 'active' AS active
        FROM nodes WHERE merged_into IS NULL
        """
    )
    topic_rows = await conn.fetch(
        """
        SELECT t.topic_id::text AS topic_id, t.name, t.origin, t.hidden,
               tn.node_id::text AS node_id, tn.added_by
        FROM topics t LEFT JOIN topic_nodes tn ON tn.topic_id = t.topic_id
        """
    )
    g = build_graph([(r["a"], r["b"], r["band"]) for r in edge_rows])
    nodes = {r["id"]: NodeInfo(r["name"], r["node_type"], r["active"]) for r in node_rows}
    topics: dict[str, TopicState] = {}
    for r in topic_rows:
        t = topics.setdefault(r["topic_id"], TopicState(r["topic_id"], r["name"], r["origin"], r["hidden"]))
        if r["node_id"] is None:
            continue
        (t.clustered_members if r["added_by"] == "clustering" else t.operator_members).add(r["node_id"])
    return g, nodes, list(topics.values())


async def recluster_topics(pool: asyncpg.Pool, *, apply: bool) -> list[TopicPlan]:
    """Plan (and with `apply`, write) one clustering run."""
    async with pool.acquire() as conn:
        g, nodes, topics = await load(conn)
        plans = plan(g, nodes, topics)
        if not apply:
            return plans
        async with conn.transaction():
            for p in plans:
                if p.action == "create":
                    p.topic_id = str(await conn.fetchval(
                        """
                        INSERT INTO topics (name, description, dimension, origin, clustered_at)
                        VALUES ($1, $2, $3, 'clustered', now())
                        RETURNING topic_id
                        """,
                        p.name, _description(p), p.dimension,
                    ))
                else:
                    # Clustered topics' descriptions and dimensions follow their
                    # community; the operator's own topics keep theirs
                    await conn.execute(
                        """
                        UPDATE topics SET clustered_at = now(), updated_at = now(),
                               description = CASE WHEN origin = 'clustered' THEN $2 ELSE description END,
                               dimension = CASE WHEN origin = 'clustered' THEN $3 ELSE dimension END
                        WHERE topic_id = $1
                        """,
                        p.topic_id, _description(p), p.dimension,
                    )
                if p.removed:
                    await conn.execute(
                        """
                        DELETE FROM topic_nodes
                        WHERE topic_id = $1 AND added_by = 'clustering' AND node_id = ANY($2::uuid[])
                        """,
                        p.topic_id, list(p.removed),
                    )
                if p.added:
                    await conn.executemany(
                        """
                        INSERT INTO topic_nodes (topic_id, node_id, notes, added_by)
                        VALUES ($1, $2, '', 'clustering')
                        ON CONFLICT DO NOTHING
                        """,
                        [(p.topic_id, nid) for nid in sorted(p.added)],
                    )
    log.info("topics.reclustered",
             created=sum(p.action == "create" for p in plans),
             updated=sum(p.action == "update" for p in plans))
    return plans


def report(plans: list[TopicPlan]) -> list[dict[str, Any]]:
    """Job-log metadata: one line per topic touched."""
    return [{"name": p.name, "action": p.action, "size": len(p.members), "active": p.active,
             "added": len(p.added), "removed": len(p.removed)} for p in plans]
