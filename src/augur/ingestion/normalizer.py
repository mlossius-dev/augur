"""
Payload normalizer.

Converts FetchResult objects (raw fetcher output) into the canonical
Payload shape for DB storage.  Also assigns a content hash used for
exact-duplicate detection.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from augur.ingestion.models import FetchResult


def normalize(fetch_result: FetchResult) -> dict[str, Any]:
    """
    Convert a FetchResult into a dict matching the `payloads` table schema.

    Returns a dict ready for asyncpg INSERT — not a Pydantic model so we
    avoid importing the full graph.models here.
    """
    content_hash = hashlib.sha256(fetch_result.raw_content.encode("utf-8")).hexdigest()

    # content_timestamp must always be set; fall back to fetched_at
    content_timestamp = fetch_result.content_timestamp or fetch_result.fetched_at

    # Augment metadata with the content hash for deduplication
    metadata = dict(fetch_result.metadata)
    metadata["content_hash"] = content_hash

    return {
        "payload_id": uuid.uuid4(),
        "source_id": fetch_result.source_id,
        "fetched_at": fetch_result.fetched_at,
        "content_timestamp": content_timestamp,
        "perspective": fetch_result.perspective,
        "content": fetch_result.raw_content,
        "content_type": fetch_result.content_type,
        "language": fetch_result.language,
        "metadata": metadata,
        "rejected": fetch_result.rejected,
        "rejected_reason": fetch_result.rejected_reason,
    }


def content_hash(text: str) -> str:
    """SHA-256 hex digest of text, for deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
