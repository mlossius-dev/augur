"""
Unit tests for the ingestion layer.

Tests:
  1. spam_filter: all rejection conditions
  2. normalizer: payload shape, content hash
  3. source_registry: YAML loading, env var expansion
  4. RssFetcher: mock HTTP response → FetchResult list
  5. FredClient: mock API response → FetchResult list
  6. UsgsClient: mock GeoJSON response → FetchResult list
  7. SearxngFetcher: mock search response → FetchResult list
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from augur.ingestion.models import FetchResult, SourceConfig
from augur.ingestion.normalizer import content_hash, normalize
from augur.ingestion.spam_filter import check as spam_check

_TS = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make_fetch_result(
    content: str = (
        "Oil prices fall sharply as OPEC increases output. "
        "Wheat futures decline on improved harvest outlook in major producing regions."
    ),
    source_id: str = "test_source",
    perspective: str = "us_eu",
) -> FetchResult:
    return FetchResult(
        source_id=source_id,
        url="https://example.com/article",
        perspective=perspective,
        raw_content=content,
        fetched_at=_TS,
        content_timestamp=_TS,
        content_type="article",
        language="en",
        metadata={"url": "https://example.com/article"},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Spam filter
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpamFilter:
    def test_clean_content_passes(self):
        fr = _make_fetch_result()
        result = spam_check(fr, known_hashes=set())
        assert not result.rejected

    def test_too_short_rejected(self):
        fr = _make_fetch_result("Short.")
        result = spam_check(fr, known_hashes=set())
        assert result.rejected
        assert "too short" in result.rejected_reason

    def test_exact_duplicate_rejected(self):
        fr = _make_fetch_result()
        h = content_hash(fr.raw_content)
        result = spam_check(fr, known_hashes={h})
        assert result.rejected
        assert "duplicate" in result.rejected_reason

    def test_high_link_density_rejected(self):
        urls = " ".join(f"https://example.com/link{i}" for i in range(20))
        fr = _make_fetch_result(urls)
        result = spam_check(fr, known_hashes=set())
        assert result.rejected
        assert "link density" in result.rejected_reason

    def test_seo_phrase_rejected(self):
        fr = _make_fetch_result(
            "This is a very important article. Subscribe to our newsletter for more updates. "
            "Oil prices are moving due to geopolitical tensions in the Middle East region."
        )
        result = spam_check(fr, known_hashes=set())
        assert result.rejected
        assert "SEO" in result.rejected_reason or "newsletter" in result.rejected_reason

    def test_rejected_flag_is_set_on_original_object(self):
        fr = _make_fetch_result("Short.")
        spam_check(fr, known_hashes=set())
        assert fr.rejected
        assert fr.rejected_reason is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Normalizer
# ═══════════════════════════════════════════════════════════════════════════════


class TestNormalizer:
    def test_normalize_produces_all_required_fields(self):
        fr = _make_fetch_result()
        payload = normalize(fr)
        required = {
            "payload_id", "source_id", "fetched_at", "content_timestamp",
            "perspective", "content", "content_type", "language", "metadata",
            "rejected", "rejected_reason",
        }
        assert required.issubset(set(payload.keys()))

    def test_payload_id_is_uuid(self):
        fr = _make_fetch_result()
        payload = normalize(fr)
        assert isinstance(payload["payload_id"], uuid.UUID)

    def test_content_hash_in_metadata(self):
        fr = _make_fetch_result()
        payload = normalize(fr)
        assert "content_hash" in payload["metadata"]

    def test_content_hash_consistent(self):
        text = "Test content for hashing."
        h1 = content_hash(text)
        h2 = content_hash(text)
        assert h1 == h2

    def test_content_hash_differs_for_different_content(self):
        assert content_hash("A") != content_hash("B")

    def test_rejected_flag_propagated(self):
        fr = _make_fetch_result()
        fr.rejected = True
        fr.rejected_reason = "test rejection"
        payload = normalize(fr)
        assert payload["rejected"] is True
        assert payload["rejected_reason"] == "test rejection"

    def test_fallback_content_timestamp(self):
        fr = _make_fetch_result()
        fr.content_timestamp = None
        payload = normalize(fr)
        assert payload["content_timestamp"] == fr.fetched_at


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Source registry
# ═══════════════════════════════════════════════════════════════════════════════


class TestSourceRegistry:
    def test_load_sources_from_yaml(self, tmp_path):
        yaml_content = """\
sources:
  - source_id: test_rss
    canonical_name: Test RSS
    url_base: https://example.com
    tier: 2
    perspective: us_eu
    languages: [en]
    access_method: rss
    access_config:
      feeds:
        - url: https://example.com/feed
          label: main
    update_cadence: hourly
    domains: [test]
    starting_source_weight: 0.6
    enabled: true
    notes: Test source
"""
        p = tmp_path / "sources.yaml"
        p.write_text(yaml_content)

        from augur.ingestion.source_registry import load_sources
        sources = load_sources(p)
        assert len(sources) == 1
        s = sources[0]
        assert s.source_id == "test_rss"
        assert s.tier == "2"
        assert s.perspective == "us_eu"
        assert s.starting_source_weight == 0.6
        assert s.enabled is True

    def test_enabled_filter(self, tmp_path):
        yaml_content = """\
sources:
  - source_id: enabled_src
    canonical_name: Enabled
    url_base: https://example.com
    tier: 2
    perspective: us_eu
    languages: [en]
    access_method: rss
    access_config: {}
    update_cadence: hourly
    domains: []
    starting_source_weight: 0.5
    enabled: true
  - source_id: disabled_src
    canonical_name: Disabled
    url_base: https://example.com
    tier: 2
    perspective: us_eu
    languages: [en]
    access_method: rss
    access_config: {}
    update_cadence: hourly
    domains: []
    starting_source_weight: 0.5
    enabled: false
"""
        p = tmp_path / "sources.yaml"
        p.write_text(yaml_content)

        from augur.ingestion.source_registry import get_enabled_sources
        sources = get_enabled_sources(p)
        assert len(sources) == 1
        assert sources[0].source_id == "enabled_src"

    def test_env_var_expansion_in_url_base(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_URL", "http://my-searxng:8080")
        yaml_content = """\
sources:
  - source_id: searxng_test
    canonical_name: SearXNG
    url_base: ${TEST_URL}
    tier: 2
    perspective: us_eu
    languages: [en]
    access_method: searxng
    access_config: {}
    update_cadence: daily
    domains: []
    starting_source_weight: 0.5
    enabled: true
"""
        p = tmp_path / "sources.yaml"
        p.write_text(yaml_content)

        from augur.ingestion.source_registry import load_sources
        sources = load_sources(p)
        assert sources[0].url_base == "http://my-searxng:8080"

    def test_real_sources_yaml_loads(self):
        """Smoke-test: the actual config/sources.yaml must load without error."""
        from augur.ingestion.source_registry import load_sources
        sources = load_sources()
        # Must have at least some sources
        assert len(sources) > 0
        # Every source must have required fields
        for s in sources:
            assert s.source_id
            assert s.perspective
            assert 0.0 <= s.starting_source_weight <= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. RssFetcher — mock HTTP
# ═══════════════════════════════════════════════════════════════════════════════

_RSS_SAMPLE = """\
<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <link>https://example.com</link>
    <item>
      <title>Oil prices drop on demand concerns</title>
      <link>https://example.com/oil-prices</link>
      <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
      <description>WTI crude fell 2% on Monday amid concerns about global demand.</description>
    </item>
    <item>
      <title>Wheat exports surge from Black Sea region</title>
      <link>https://example.com/wheat-exports</link>
      <pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate>
      <description>Ukraine wheat exports reached 5 million tonnes last month.</description>
    </item>
  </channel>
</rss>
"""


class TestRssFetcher:
    @pytest.mark.asyncio
    async def test_fetch_source_returns_entries(self):
        source = SourceConfig(
            source_id="test_rss",
            canonical_name="Test",
            url_base="https://example.com",
            tier="2",
            perspective="us_eu",
            languages=["en"],
            access_method="rss",
            access_config={"feeds": [{"url": "https://example.com/feed", "label": "main"}]},
            update_cadence="hourly",
            domains=["commodities"],
            starting_source_weight=0.6,
        )

        mock_response = MagicMock()
        mock_response.text = _RSS_SAMPLE
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from augur.ingestion.fetchers.rss import RssFetcher
            fetcher = RssFetcher()
            results = await fetcher.fetch_source(source)

        assert len(results) == 2
        assert results[0].source_id == "test_rss"
        assert results[0].perspective == "us_eu"
        assert "Oil prices" in results[0].raw_content
        assert results[0].content_timestamp is not None

    @pytest.mark.asyncio
    async def test_fetch_source_handles_network_failure(self):
        source = SourceConfig(
            source_id="test_rss",
            canonical_name="Test",
            url_base="https://example.com",
            tier="2",
            perspective="us_eu",
            languages=["en"],
            access_method="rss",
            access_config={"feeds": [{"url": "https://example.com/feed", "label": "main"}]},
            update_cadence="hourly",
            domains=["commodities"],
            starting_source_weight=0.6,
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from augur.ingestion.fetchers.rss import RssFetcher
            fetcher = RssFetcher()
            results = await fetcher.fetch_source(source)

        # Failures should return empty list, not raise
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# 5. FredClient — mock API
# ═══════════════════════════════════════════════════════════════════════════════

_FRED_RESPONSE = {
    "observations": [
        {"date": "2024-01-15", "value": "75.32"},
        {"date": "2024-01-12", "value": "74.10"},
        {"date": "2024-01-11", "value": "72.85"},
    ]
}


class TestFredClient:
    @pytest.mark.asyncio
    async def test_fetch_source_returns_results(self):
        source = SourceConfig(
            source_id="fred",
            canonical_name="FRED",
            url_base="https://api.stlouisfed.org",
            tier="structured_data",
            perspective="us_eu",
            languages=["en"],
            access_method="api",
            access_config={"series_ids": ["DCOILWTICO"]},
            update_cadence="daily",
            domains=["commodities"],
            starting_source_weight=0.95,
        )

        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value=_FRED_RESPONSE)
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from augur.ingestion.api_clients.fred import FredClient
            client = FredClient()
            results = await client.fetch_source(source)

        assert len(results) == 1
        assert "DCOILWTICO" in results[0].raw_content
        assert results[0].content_type == "structured_feed_entry"
        assert results[0].metadata["latest_value"] == "75.32"

    @pytest.mark.asyncio
    async def test_skips_missing_values(self):
        source = SourceConfig(
            source_id="fred",
            canonical_name="FRED",
            url_base="https://api.stlouisfed.org",
            tier="structured_data",
            perspective="us_eu",
            languages=["en"],
            access_method="api",
            access_config={"series_ids": ["DCOILWTICO"]},
            update_cadence="daily",
            domains=["commodities"],
            starting_source_weight=0.95,
        )

        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value={"observations": [{"date": "2024-01-15", "value": "."}]})
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from augur.ingestion.api_clients.fred import FredClient
            client = FredClient()
            results = await client.fetch_source(source)

        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# 6. UsgsClient — mock GeoJSON
# ═══════════════════════════════════════════════════════════════════════════════

_USGS_RESPONSE = {
    "features": [
        {
            "id": "us2024abc",
            "properties": {
                "mag": 6.2,
                "magType": "Mw",
                "place": "150 km SE of Tokyo, Japan",
                "time": 1704067200000,
                "url": "https://earthquake.usgs.gov/earthquakes/eventpage/us2024abc",
                "ids": ",us2024abc,",
            },
            "geometry": {
                "coordinates": [140.12, 34.56, 35.0]
            },
        }
    ]
}


class TestUsgsClient:
    @pytest.mark.asyncio
    async def test_fetch_source_returns_earthquake(self):
        source = SourceConfig(
            source_id="usgs_earthquakes",
            canonical_name="USGS",
            url_base="https://earthquake.usgs.gov",
            tier="structured_data",
            perspective="us_eu",
            languages=["en"],
            access_method="api",
            access_config={
                "endpoint": "/fdsnws/event/1/query",
                "default_params": {"format": "geojson", "minmagnitude": 5.0, "limit": 100},
            },
            update_cadence="hourly",
            domains=["physical_world"],
            starting_source_weight=1.0,
        )

        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value=_USGS_RESPONSE)
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from augur.ingestion.api_clients.usgs import UsgsClient
            client = UsgsClient()
            results = await client.fetch_source(source)

        assert len(results) == 1
        assert "Mw6.2" in results[0].raw_content
        assert "Tokyo" in results[0].raw_content
        assert results[0].metadata["magnitude"] == 6.2


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SearxngFetcher — mock search
# ═══════════════════════════════════════════════════════════════════════════════

_SEARXNG_RESPONSE = {
    "results": [
        {
            "title": "Global wheat prices surge on export restrictions",
            "content": "Wheat futures hit a 6-month high as major exporters impose curbs.",
            "url": "https://example.com/wheat-story",
            "engine": "google news",
        },
        {
            "title": "Oil demand forecasts cut by IEA",
            "content": "The IEA revised its demand forecast downward citing slower growth.",
            "url": "https://example.com/iea-story",
            "engine": "bing news",
        },
    ]
}


class TestSearxngFetcher:
    @pytest.mark.asyncio
    async def test_fetch_source_returns_results(self):
        source = SourceConfig(
            source_id="searxng_commodities",
            canonical_name="SearXNG Commodities",
            url_base="http://searxng:8080",
            tier="2",
            perspective="us_eu",
            languages=["en"],
            access_method="searxng",
            access_config={
                "queries": [{"template": "topic_recent", "topic": "commodity prices", "categories": ["news"]}],
                "max_results_per_query": 10,
            },
            update_cadence="daily",
            domains=["commodities"],
            starting_source_weight=0.5,
        )

        mock_response = MagicMock()
        mock_response.json = MagicMock(return_value=_SEARXNG_RESPONSE)
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            from augur.ingestion.fetchers.searxng import SearxngFetcher
            fetcher = SearxngFetcher("http://searxng:8080")
            results = await fetcher.fetch_source(source)

        assert len(results) == 2
        assert "wheat" in results[0].raw_content.lower()
