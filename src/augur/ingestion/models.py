"""
Ingestion layer data models.

These models live at the ingestion boundary — they represent raw
fetched content (FetchResult) and the source registry entries (SourceConfig).
They are distinct from the DB-level Payload model in augur.graph.models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class FetchResult:
    """
    The raw output of a single fetch operation.

    Produced by all fetchers; normalizer converts this to a Payload.
    """

    source_id: str
    # The URL or API endpoint that was fetched
    url: str
    # The perspective pool this source belongs to
    perspective: str
    # Raw content: text body, JSON, or structured data
    raw_content: str
    # When Augur fetched the content (always now())
    fetched_at: datetime
    # When the content was published/observed (from HTTP headers, RSS pubDate, API fields)
    content_timestamp: datetime | None
    # "article" | "api_response" | "structured_feed_entry" | "dataset_row"
    content_type: str = "article"
    # ISO 639-1
    language: str | None = None
    # Arbitrary metadata: headline, native_id, raw_response, etc.
    metadata: dict[str, Any] = field(default_factory=dict)
    # Set by spam_filter if this fetch should be dropped
    rejected: bool = False
    rejected_reason: str | None = None


@dataclass
class SourceConfig:
    """
    A parsed entry from config/sources.yaml.

    Loaded once at startup by the source registry.
    """

    source_id: str
    canonical_name: str
    url_base: str
    tier: str  # "0" | "1" | "2" | "3" | "4" | "structured_data"
    perspective: str
    languages: list[str]
    access_method: str  # "http" | "rss" | "api" | "searxng"
    access_config: dict[str, Any]
    update_cadence: str
    domains: list[str]
    starting_source_weight: float
    enabled: bool = True
    notes: str = ""
