"""
Source registry — loads config/sources.yaml and provides typed access.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from augur.ingestion.models import SourceConfig

_DEFAULT_SOURCES_PATH = Path(__file__).parent.parent.parent.parent / "config" / "sources.yaml"


def load_sources(path: Path | str | None = None) -> list[SourceConfig]:
    """
    Load and parse the source registry YAML file.

    Environment variables in url_base (e.g. ${SEARXNG_URL}) are expanded.
    Disabled sources (enabled: false) are included — callers decide whether
    to skip them.
    """
    p = Path(path) if path else _DEFAULT_SOURCES_PATH
    raw = yaml.safe_load(p.read_text())

    sources = []
    for entry in raw.get("sources", []):
        # Expand env vars in url_base
        url_base = os.path.expandvars(entry.get("url_base", ""))
        sources.append(
            SourceConfig(
                source_id=entry["source_id"],
                canonical_name=entry["canonical_name"],
                url_base=url_base,
                tier=str(entry.get("tier", "2")),
                perspective=entry.get("perspective", "us_eu"),
                languages=entry.get("languages", ["en"]),
                access_method=entry.get("access_method", "rss"),
                access_config=entry.get("access_config", {}),
                update_cadence=entry.get("update_cadence", "daily"),
                domains=entry.get("domains", []),
                starting_source_weight=float(entry.get("starting_source_weight", 0.5)),
                enabled=entry.get("enabled", True),
                notes=entry.get("notes", ""),
            )
        )
    return sources


def get_enabled_sources(path: Path | str | None = None) -> list[SourceConfig]:
    """Return only enabled sources."""
    return [s for s in load_sources(path) if s.enabled]
