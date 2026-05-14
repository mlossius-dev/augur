"""
Phase 9 tests — topic view and geographic scoping.

Covers:
  - presentation/topics.py  — CRUD + aggregation
  - presentation/geo.py     — region inference + scoped response
  - api/topics.py           — list and detail endpoints
  - api/geo.py              — scope endpoint
  - cli topics commands     — smoke-test argument parsing
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_pool(*fetch_results, fetchrow_result=None, execute_result="INSERT 0 1"):
    """Build a minimal asyncpg pool mock."""
    pool = MagicMock()
    conn = AsyncMock()

    call_count = {"n": 0}
    async def fetch_side_effect(query, *args):
        idx = call_count["n"]
        call_count["n"] += 1
        if idx < len(fetch_results):
            return fetch_results[idx]
        return []

    conn.fetch.side_effect = fetch_side_effect
    conn.fetchrow.return_value = fetchrow_result
    conn.execute.return_value = execute_result
    conn.executemany.return_value = execute_result

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool, conn


def _row(**kwargs):
    """Create a dict-like row."""
    return kwargs


# ── Topics presentation layer ─────────────────────────────────────────────────


class TestGetTopicList:
    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_topics(self):
        from augur.presentation.topics import get_topic_list

        pool, _ = _make_pool([])
        result = await get_topic_list(pool)
        assert result == []

    @pytest.mark.asyncio
    async def test_maps_row_to_topic_summary(self):
        from augur.presentation.topics import get_topic_list

        tid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        row = {
            "topic_id": tid,
            "name": "Gas supply",
            "description": "European gas",
            "dimension": "resource_availability",
            "created_at": now,
            "updated_at": now,
            "node_count": 5,
            "active_count": 2,
        }
        pool, _ = _make_pool([row])
        result = await get_topic_list(pool)
        assert len(result) == 1
        t = result[0]
        assert t.topic_id == str(tid)
        assert t.name == "Gas supply"
        assert t.node_count == 5
        assert t.active_condition_count == 2
        assert t.state in ("improving", "stable", "strained", "deteriorating", "crisis", "unknown")

    @pytest.mark.asyncio
    async def test_state_is_stable_for_low_active_ratio(self):
        from augur.presentation.topics import get_topic_list

        tid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        row = {
            "topic_id": tid, "name": "T", "description": "", "dimension": None,
            "created_at": now, "updated_at": now,
            "node_count": 10, "active_count": 3,  # 30% → stable
        }
        pool, _ = _make_pool([row])
        result = await get_topic_list(pool)
        assert result[0].state == "stable"

    @pytest.mark.asyncio
    async def test_state_is_crisis_when_all_active(self):
        from augur.presentation.topics import get_topic_list

        tid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        row = {
            "topic_id": tid, "name": "T", "description": "", "dimension": None,
            "created_at": now, "updated_at": now,
            "node_count": 10, "active_count": 9,  # 90% → crisis
        }
        pool, _ = _make_pool([row])
        result = await get_topic_list(pool)
        assert result[0].state == "crisis"

    @pytest.mark.asyncio
    async def test_state_is_unknown_when_no_nodes(self):
        from augur.presentation.topics import get_topic_list

        tid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        row = {
            "topic_id": tid, "name": "T", "description": "", "dimension": None,
            "created_at": now, "updated_at": now,
            "node_count": 0, "active_count": 0,
        }
        pool, _ = _make_pool([row])
        result = await get_topic_list(pool)
        assert result[0].state == "unknown"


class TestGetTopicDetail:
    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_topic(self):
        from augur.presentation.topics import get_topic_detail

        pool, conn = _make_pool([])
        conn.fetchrow.return_value = None
        result = await get_topic_detail(pool, str(uuid.uuid4()))
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_detail_with_nodes(self):
        from augur.presentation.topics import get_topic_detail

        tid = uuid.uuid4()
        nid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        topic_row = {
            "topic_id": tid, "name": "Gas supply", "description": "desc",
            "dimension": "resource_availability", "created_at": now, "updated_at": now,
        }
        node_rows = [
            {
                "node_id": nid, "name": "EU Gas Storage", "node_type": "condition",
                "current_state": "active", "added_at": now, "notes": "",
            }
        ]
        pool, conn = _make_pool(node_rows)
        conn.fetchrow.return_value = topic_row

        result = await get_topic_detail(pool, str(tid))
        assert result is not None
        assert result.name == "Gas supply"
        assert len(result.nodes) == 1
        assert result.nodes[0].name == "EU Gas Storage"
        assert result.active_condition_count == 1

    @pytest.mark.asyncio
    async def test_active_count_only_includes_active_conditions(self):
        from augur.presentation.topics import get_topic_detail

        tid = uuid.uuid4()
        now = datetime.now(timezone.utc)
        topic_row = {
            "topic_id": tid, "name": "T", "description": "", "dimension": None,
            "created_at": now, "updated_at": now,
        }
        node_rows = [
            {"node_id": uuid.uuid4(), "name": "A", "node_type": "condition",
             "current_state": "active", "added_at": now, "notes": ""},
            {"node_id": uuid.uuid4(), "name": "B", "node_type": "condition",
             "current_state": "inactive", "added_at": now, "notes": ""},
            {"node_id": uuid.uuid4(), "name": "C", "node_type": "entity",
             "current_state": None, "added_at": now, "notes": ""},
        ]
        pool, conn = _make_pool(node_rows)
        conn.fetchrow.return_value = topic_row
        result = await get_topic_detail(pool, str(tid))
        assert result.node_count == 3
        assert result.active_condition_count == 1


class TestCreateTopic:
    @pytest.mark.asyncio
    async def test_returns_topic_id_string(self):
        from augur.presentation.topics import create_topic

        new_id = uuid.uuid4()
        pool, conn = _make_pool()
        conn.fetchrow.return_value = {"topic_id": new_id}

        result = await create_topic(pool, name="Energy Crisis", description="desc", dimension="resource_availability")
        assert result == str(new_id)

    @pytest.mark.asyncio
    async def test_create_without_dimension(self):
        from augur.presentation.topics import create_topic

        new_id = uuid.uuid4()
        pool, conn = _make_pool()
        conn.fetchrow.return_value = {"topic_id": new_id}
        result = await create_topic(pool, name="Misc")
        assert result == str(new_id)


class TestAssignNodes:
    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_list(self):
        from augur.presentation.topics import assign_nodes_to_topic

        pool, _ = _make_pool()
        result = await assign_nodes_to_topic(pool, topic_id=str(uuid.uuid4()), node_ids=[])
        assert result == 0

    @pytest.mark.asyncio
    async def test_calls_executemany(self):
        from augur.presentation.topics import assign_nodes_to_topic

        pool, conn = _make_pool(execute_result="INSERT 0 2")
        conn.executemany.return_value = "INSERT 0 2"
        nids = [str(uuid.uuid4()), str(uuid.uuid4())]
        result = await assign_nodes_to_topic(pool, topic_id=str(uuid.uuid4()), node_ids=nids)
        assert conn.executemany.called


class TestRemoveNodes:
    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_list(self):
        from augur.presentation.topics import remove_nodes_from_topic

        pool, _ = _make_pool()
        result = await remove_nodes_from_topic(pool, topic_id=str(uuid.uuid4()), node_ids=[])
        assert result == 0

    @pytest.mark.asyncio
    async def test_calls_execute(self):
        from augur.presentation.topics import remove_nodes_from_topic

        pool, conn = _make_pool()
        conn.execute.return_value = "DELETE 1"
        result = await remove_nodes_from_topic(
            pool, topic_id=str(uuid.uuid4()), node_ids=[str(uuid.uuid4())]
        )
        assert conn.execute.called


class TestListTopicsForNode:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        from augur.presentation.topics import list_topics_for_node

        now = datetime.now(timezone.utc)
        rows = [
            {"topic_id": uuid.uuid4(), "name": "T1", "description": "",
             "dimension": None, "added_at": now, "notes": ""},
        ]
        pool, _ = _make_pool(rows)
        result = await list_topics_for_node(pool, str(uuid.uuid4()))
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_topics(self):
        from augur.presentation.topics import list_topics_for_node

        pool, _ = _make_pool([])
        result = await list_topics_for_node(pool, str(uuid.uuid4()))
        assert result == []


# ── Geo presentation layer ────────────────────────────────────────────────────


class TestInferRegion:
    def _regions(self):
        return [
            {
                "region_id": "europe_west",
                "display_name": "Western Europe",
                "perspectives": ["us_eu"],
                "entity_keywords": ["europe", "eu"],
                "lat_min": 35.0, "lat_max": 70.0,
                "lon_min": -10.0, "lon_max": 25.0,
            },
            {
                "region_id": "nordic",
                "display_name": "Nordic",
                "perspectives": ["us_eu"],
                "entity_keywords": ["norway", "sweden"],
                "lat_min": 54.5, "lat_max": 71.5,
                "lon_min": 4.0, "lon_max": 32.0,
            },
            {
                "region_id": "global",
                "display_name": "Global",
                "perspectives": ["us_eu"],
                "entity_keywords": [],
                "lat_min": -90.0, "lat_max": 90.0,
                "lon_min": -180.0, "lon_max": 180.0,
            },
        ]

    def test_point_in_only_one_region(self):
        from augur.presentation.geo import infer_region

        regions = self._regions()
        result = infer_region(51.5, 0.1, regions)  # London
        assert result["region_id"] == "europe_west"

    def test_point_in_overlapping_prefers_smallest(self):
        from augur.presentation.geo import infer_region

        regions = self._regions()
        result = infer_region(60.0, 10.0, regions)  # Oslo — in both nordic and europe_west
        # nordic has smaller area than europe_west
        assert result["region_id"] == "nordic"

    def test_falls_back_to_global(self):
        from augur.presentation.geo import infer_region

        regions = self._regions()
        result = infer_region(-34.0, 18.5, regions)  # Cape Town — no specific region
        assert result["region_id"] == "global"

    def test_returns_none_when_no_regions(self):
        from augur.presentation.geo import infer_region

        result = infer_region(0.0, 0.0, [])
        assert result is None

    def test_exact_boundary_point_is_inclusive(self):
        from augur.presentation.geo import infer_region

        regions = self._regions()
        result = infer_region(35.0, -10.0, regions)  # exactly on europe_west boundary
        assert result is not None
        assert result["region_id"] in ("europe_west", "global")

    def test_antimeridian_point(self):
        from augur.presentation.geo import infer_region

        regions = self._regions()
        result = infer_region(0.0, 170.0, regions)  # Pacific
        assert result["region_id"] == "global"


class TestLoadRegionDefinitions:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        from augur.presentation.geo import load_region_definitions

        rows = [
            {
                "region_id": "nordic", "display_name": "Nordic",
                "perspectives": ["us_eu"], "entity_keywords": ["norway"],
                "lat_min": 54.5, "lat_max": 71.5, "lon_min": 4.0, "lon_max": 32.0,
            }
        ]
        pool, _ = _make_pool(rows)
        result = await load_region_definitions(pool)
        assert len(result) == 1
        assert result[0]["region_id"] == "nordic"


class TestGetRegionalScope:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_regions(self):
        from augur.presentation.geo import get_regional_scope

        pool, _ = _make_pool([])  # empty region defs

        with patch("augur.presentation.dimensions.compute_dimension_scores", new_callable=AsyncMock) as md, \
             patch("augur.presentation.changes.get_recent_changes", new_callable=AsyncMock) as mc:
            md.return_value = []
            mc.return_value = []
            result = await get_regional_scope(pool, 0.0, 0.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_scope_with_dimensions_and_changes(self):
        from augur.presentation.geo import get_regional_scope

        regions = [
            {
                "region_id": "global", "display_name": "Global",
                "perspectives": ["us_eu"], "entity_keywords": [],
                "lat_min": -90.0, "lat_max": 90.0,
                "lon_min": -180.0, "lon_max": 180.0,
            }
        ]
        pool, _ = _make_pool(regions)

        fake_dim = MagicMock()
        fake_change = MagicMock()
        fake_change.target_name = "test"
        fake_change.summary = "test summary"

        with patch("augur.presentation.dimensions.compute_dimension_scores", new_callable=AsyncMock) as md, \
             patch("augur.presentation.changes.get_recent_changes", new_callable=AsyncMock) as mc:
            md.return_value = [fake_dim]
            mc.return_value = [fake_change]
            result = await get_regional_scope(pool, 0.0, 0.0)

        assert result is not None
        assert result.region.region_id == "global"
        assert len(result.dimensions) == 1
        assert len(result.changes) == 1

    @pytest.mark.asyncio
    async def test_entity_keywords_filter_changes(self):
        from augur.presentation.geo import get_regional_scope

        regions = [
            {
                "region_id": "europe_west", "display_name": "Western Europe",
                "perspectives": ["us_eu"], "entity_keywords": ["euro", "ecb"],
                "lat_min": 35.0, "lat_max": 70.0, "lon_min": -10.0, "lon_max": 25.0,
            },
            {
                "region_id": "global", "display_name": "Global",
                "perspectives": ["us_eu"], "entity_keywords": [],
                "lat_min": -90.0, "lat_max": 90.0, "lon_min": -180.0, "lon_max": 180.0,
            },
        ]
        pool, _ = _make_pool(regions)

        matching_change = MagicMock()
        matching_change.target_name = "ECB rate decision"
        matching_change.summary = "Euro zone ECB raised rates"

        non_matching_change = MagicMock()
        non_matching_change.target_name = "India GDP growth"
        non_matching_change.summary = "India growth accelerated"

        with patch("augur.presentation.dimensions.compute_dimension_scores", new_callable=AsyncMock) as md, \
             patch("augur.presentation.changes.get_recent_changes", new_callable=AsyncMock) as mc:
            md.return_value = []
            mc.return_value = [matching_change, non_matching_change]
            result = await get_regional_scope(pool, 51.5, 0.1)  # London → europe_west

        assert result is not None
        assert len(result.changes) == 1
        assert result.changes[0].target_name == "ECB rate decision"


# ── API topics ────────────────────────────────────────────────────────────────


def _make_topics_client(mock_pool=None):
    """Build a TestClient for the topics router with pool dependency overridden."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import augur.api.topics as topics_mod
    from augur.api.topics import router

    app = FastAPI()
    app.include_router(router)
    pool = mock_pool or MagicMock()
    app.dependency_overrides[topics_mod._pool] = lambda: pool
    return TestClient(app), pool


class TestTopicsApiList:
    def test_returns_200_with_empty_list(self):
        client, _ = _make_topics_client()
        with patch("augur.api.topics.get_topic_list", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = []
            resp = client.get("/api/topics")
        assert resp.status_code == 200
        assert resp.json()["topics"] == []

    def test_serialises_topic_fields(self):
        from augur.presentation.topics import TopicSummary

        now = datetime.now(timezone.utc).isoformat()
        t = TopicSummary(
            topic_id=str(uuid.uuid4()), name="Gas",
            description="desc", dimension="resource_availability",
            node_count=3, active_condition_count=1,
            state="stable", created_at=now, updated_at=now,
        )
        client, _ = _make_topics_client()
        with patch("augur.api.topics.get_topic_list", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = [t]
            resp = client.get("/api/topics")
        assert resp.status_code == 200
        data = resp.json()["topics"]
        assert len(data) == 1
        assert data[0]["name"] == "Gas"
        assert data[0]["state"] == "stable"


class TestTopicsApiDetail:
    def _make_detail(self):
        from augur.presentation.topics import TopicDetail, TopicNodeSummary

        now = datetime.now(timezone.utc).isoformat()
        node = TopicNodeSummary(
            node_id=str(uuid.uuid4()), name="EU Gas",
            node_type="condition", current_state="active",
            added_at=now, notes="",
        )
        return TopicDetail(
            topic_id=str(uuid.uuid4()), name="Gas supply",
            description="", dimension="resource_availability",
            node_count=1, active_condition_count=1,
            state="crisis", created_at=now, updated_at=now,
            nodes=[node],
        )

    def test_returns_404_for_unknown(self):
        client, _ = _make_topics_client()
        with patch("augur.api.topics.get_topic_detail", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = None
            resp = client.get(f"/api/topics/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_returns_detail_with_nodes(self):
        detail = self._make_detail()
        client, _ = _make_topics_client()
        with patch("augur.api.topics.get_topic_detail", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = detail
            resp = client.get(f"/api/topics/{detail.topic_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Gas supply"
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["current_state"] == "active"

    def test_rejects_bad_as_of(self):
        client, _ = _make_topics_client()
        with patch("augur.api.topics.get_topic_detail", new_callable=AsyncMock):
            resp = client.get(f"/api/topics/{uuid.uuid4()}?as_of=not-a-date")
        assert resp.status_code == 422


# ── API geo ───────────────────────────────────────────────────────────────────


def _make_geo_client(mock_pool=None):
    """Build a TestClient for the geo router with pool dependency overridden."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import augur.api.geo as geo_mod
    from augur.api.geo import router

    app = FastAPI()
    app.include_router(router)
    pool = mock_pool or MagicMock()
    app.dependency_overrides[geo_mod._pool] = lambda: pool
    return TestClient(app), pool


class TestGeoApi:
    def _scope(self):
        from augur.presentation.geo import GeoScopeResponse, RegionScope

        region = RegionScope(
            region_id="europe_west",
            display_name="Western Europe",
            perspectives=["us_eu"],
            entity_keywords=["europe"],
            lat=51.5, lon=0.1,
        )
        return GeoScopeResponse(region=region, dimensions=[], changes=[], as_of=None)

    def test_requires_lat_and_lon(self):
        client, _ = _make_geo_client()
        resp = client.get("/api/geo/scope")
        assert resp.status_code == 422

    def test_returns_scope_response(self):
        scope = self._scope()
        client, _ = _make_geo_client()
        with patch("augur.api.geo.get_regional_scope", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = scope
            resp = client.get("/api/geo/scope?lat=51.5&lon=0.1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["region"]["region_id"] == "europe_west"
        assert "dimensions" in data
        assert "changes" in data

    def test_returns_404_when_no_region(self):
        client, _ = _make_geo_client()
        with patch("augur.api.geo.get_regional_scope", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = None
            resp = client.get("/api/geo/scope?lat=0&lon=0")
        assert resp.status_code == 404

    def test_lat_out_of_range(self):
        client, _ = _make_geo_client()
        resp = client.get("/api/geo/scope?lat=100&lon=0")
        assert resp.status_code == 422

    def test_lon_out_of_range(self):
        client, _ = _make_geo_client()
        resp = client.get("/api/geo/scope?lat=0&lon=200")
        assert resp.status_code == 422

    def test_rejects_bad_as_of(self):
        scope = self._scope()
        client, _ = _make_geo_client()
        with patch("augur.api.geo.get_regional_scope", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = scope
            resp = client.get("/api/geo/scope?lat=51.5&lon=0.1&as_of=bad-date")
        assert resp.status_code == 422


# ── Derive topic state ────────────────────────────────────────────────────────


class TestDeriveTopicState:
    def test_unknown_for_zero_total(self):
        from augur.presentation.topics import _derive_topic_state
        assert _derive_topic_state(0, 0) == "unknown"

    def test_improving_for_zero_active(self):
        from augur.presentation.topics import _derive_topic_state
        assert _derive_topic_state(0, 10) == "improving"

    def test_crisis_for_all_active(self):
        from augur.presentation.topics import _derive_topic_state
        assert _derive_topic_state(10, 10) == "crisis"

    def test_strained_for_mid_ratio(self):
        from augur.presentation.topics import _derive_topic_state
        # 50% → strained (0.40 ≤ ratio < 0.60)
        assert _derive_topic_state(5, 10) == "strained"


# ── Main router registration ──────────────────────────────────────────────────


class TestRouterRegistration:
    def test_topics_and_geo_routers_importable(self):
        from augur.api.topics import router as topics_router
        from augur.api.geo import router as geo_router

        assert topics_router is not None
        assert geo_router is not None

    def test_topics_router_has_expected_routes(self):
        from augur.api.topics import router

        paths = {r.path for r in router.routes}
        assert "/api/topics" in paths
        assert "/api/topics/{topic_id}" in paths

    def test_geo_router_has_scope_route(self):
        from augur.api.geo import router

        paths = {r.path for r in router.routes}
        assert "/api/geo/scope" in paths
