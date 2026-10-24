"""
Topics from graph clustering (docs/augur-presentation.md, "How topics emerge"):
communities, matching existing topics, new topics, operator curation.
"""

from __future__ import annotations

from augur.presentation.clustering import (
    NodeInfo,
    TopicState,
    build_graph,
    communities,
    match,
    plan,
)


def _clique(prefix: str, n: int, band: str = "moderate") -> list[tuple[str, str, str]]:
    ids = [f"{prefix}{i}" for i in range(n)]
    return [(a, b, band) for i, a in enumerate(ids) for b in ids[i + 1:]]


def _world() -> tuple:
    # Two dense groups joined by one weak edge, plus a small pair
    edges = _clique("oil", 12) + _clique("war", 11) + [("oil0", "war0", "weak"), ("x0", "x1", "strong")]
    g = build_graph(edges)
    nodes = {n: NodeInfo(name=n, node_type="condition" if n.endswith(("1", "2")) else "entity",
                         active=n.endswith(("1", "2")))
             for n in g.nodes}
    nodes["oil0"].name, nodes["war0"].name = "Crude Oil", "Russia"
    return g, nodes


def test_division_is_stable_for_an_unchanged_graph() -> None:
    edges = _clique("oil", 12) + _clique("war", 11) + [("oil0", "war0", "weak")]
    first = communities(build_graph(edges))
    assert first == communities(build_graph(list(reversed(edges)))), "input order does not matter"
    g, _ = _world()
    assert {frozenset(c) for c in first} >= {frozenset(f"oil{i}" for i in range(12)),
                                             frozenset(f"war{i}" for i in range(11))}


def test_parallel_edges_add_their_band_weights() -> None:
    g = build_graph([("a", "b", "strong"), ("a", "b", "weak"), ("b", "b", "strong")])
    assert g["a"]["b"]["weight"] == 4.0
    assert not g.has_edge("b", "b"), "self-loops are ignored"


def test_an_existing_topic_follows_the_community_holding_most_of_its_members() -> None:
    g, nodes = _world()
    oil = TopicState("t-oil", "Oil supply", "operator", False, operator_members={"oil0", "oil1", "war5"})
    [update] = [p for p in plan(g, nodes, [oil]) if p.action == "update"]
    assert update.topic_id == "t-oil" and update.name == "Oil supply"
    assert "oil11" in update.added, "the topic grows with its community"
    assert "oil0" not in update.members, "operator members stay operator rows"


def test_clustering_removes_only_its_own_members() -> None:
    g, nodes = _world()
    oil = TopicState("t-oil", "Oil supply", "operator", False,
                     operator_members={"oil0", "oil1", "war7"}, clustered_members={"oil3", "war8"})
    [update] = [p for p in plan(g, nodes, [oil]) if p.topic_id == "t-oil"]
    assert update.removed == {"war8"}
    assert "war7" not in update.removed, "an operator member is never removed"


def test_one_community_per_topic_and_one_topic_per_community() -> None:
    g, _ = _world()
    comms = communities(g)
    a = TopicState("a", "A", "operator", False, operator_members={f"oil{i}" for i in range(10)})
    b = TopicState("b", "B", "operator", False, operator_members={"oil0", "oil1"})
    out = match([a, b], comms)
    assert len(set(out.values())) == len(out)
    assert "a" in out


def test_a_topic_below_the_match_share_is_left_alone() -> None:
    g, nodes = _world()
    scattered = TopicState("s", "Scattered", "operator", False,
                           operator_members={"oil0", "a", "b", "c", "d", "e", "f", "g", "h", "i"})
    assert "s" not in {p.topic_id for p in plan(g, nodes, [scattered])}


def test_unmatched_large_communities_with_active_conditions_become_topics() -> None:
    g, nodes = _world()
    created = [p for p in plan(g, nodes, []) if p.action == "create"]
    assert len(created) == 2, "the small pair is too small"
    names = {p.name for p in created}
    assert any("Crude Oil" in n for n in names) and any("Russia" in n for n in names)
    assert all(p.active >= 2 for p in created)


def test_a_community_without_active_conditions_is_not_a_topic() -> None:
    g, nodes = _world()
    for n in nodes.values():
        n.active = False
    assert [p for p in plan(g, nodes, []) if p.action == "create"] == []


def test_a_hidden_topic_absorbs_its_community_so_it_is_not_recreated() -> None:
    g, nodes = _world()
    hidden = TopicState("h", "Sport", "clustered", True, clustered_members={f"war{i}" for i in range(11)})
    plans = plan(g, nodes, [hidden])
    assert [p.hidden for p in plans if p.topic_id == "h"] == [True]
    assert not any(p.action == "create" and "Russia" in p.name for p in plans)


def test_new_names_do_not_collide_with_existing_topics() -> None:
    g, nodes = _world()
    first = {p.name for p in plan(g, nodes, []) if p.action == "create"}
    taken = [TopicState(f"t{i}", n, "operator", True) for i, n in enumerate(first)]
    second = {p.name for p in plan(g, nodes, taken) if p.action == "create"}
    assert not first & second


def test_a_name_skips_repeated_names_and_prefers_entities_to_events() -> None:
    from augur.presentation.clustering import _name

    nodes = {"e": NodeInfo("Israeli strikes on Gaza, 30 September 2026", "event"),
             "a": NodeInfo("USD/CAD exchange rate", "entity"),
             "b": NodeInfo("usd/cad exchange rate", "entity"),
             "c": NodeInfo("Canada", "entity")}
    assert _name(["e", "a", "b", "c"], nodes) == "USD/CAD exchange rate · Canada"


def test_a_dimension_needs_a_keyword_and_none_is_none() -> None:
    from augur.presentation.clustering import _dimension

    assert _dimension({"a"}, {"a": NodeInfo("Manchester City", "entity")}) is None
    assert _dimension({"a", "b"}, {"a": NodeInfo("Crude oil supply", "condition"),
                                   "b": NodeInfo("Diesel shortage", "condition")}) == "resource_availability"


def test_a_small_topic_inside_a_large_community_does_not_take_it() -> None:
    """The topic that already is most of the community keeps it (October 2026: a
    10-node sanctions topic took the 234-node US–Iran community)."""
    g, _ = _world()
    comms = communities(g)
    big = TopicState("big", "Oil", "clustered", False, clustered_members={f"oil{i}" for i in range(11)})
    small = TopicState("small", "Oil sanctions", "clustered", False, clustered_members={"oil0", "oil1"})
    out = match([small, big], comms)
    assert comms[out["big"]] >= {"oil0", "oil1"}
    assert "small" not in out or comms[out["small"]] != comms[out["big"]]
