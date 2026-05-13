"""
Lightweight spam/junk filter applied before paying for LLM extraction.

Checks:
1. Minimum content length.
2. Exact-duplicate detection via content hash against recently ingested payloads.
3. Basic SEO/noise heuristics (link density, formulaic phrasing).

Rejected payloads are flagged (rejected=True, rejected_reason set) but not
dropped outright — the pipeline still archives them with the rejection flag.
"""

from __future__ import annotations

import re

from augur.ingestion.models import FetchResult
from augur.ingestion.normalizer import content_hash

# Minimum character count for a useful payload
_MIN_LENGTH = 80

# Phrases that indicate low-value SEO content
_SEO_PHRASES = [
    "click here to read more",
    "subscribe to our newsletter",
    "sign up for our newsletter",
    "terms of service",
    "privacy policy",
    "all rights reserved",
    "powered by wordpress",
    "cookie policy",
]

# Maximum fraction of the text that can be URLs before we call it link-spam
_MAX_LINK_DENSITY = 0.40

_URL_RE = re.compile(r"https?://\S+")


def check(fetch_result: FetchResult, *, known_hashes: set[str]) -> FetchResult:
    """
    Apply spam checks to `fetch_result`.

    Returns a (possibly mutated) FetchResult with rejected/rejected_reason set
    if any check fails.  The result is always returned — rejection is a flag,
    not an exception.
    """
    text = fetch_result.raw_content

    # 1. Minimum length
    if len(text) < _MIN_LENGTH:
        return _reject(fetch_result, f"content too short ({len(text)} chars < {_MIN_LENGTH})")

    # 2. Exact-duplicate check
    h = content_hash(text)
    if h in known_hashes:
        return _reject(fetch_result, "exact duplicate of already-ingested content")

    # 3. Link density
    urls = _URL_RE.findall(text)
    url_chars = sum(len(u) for u in urls)
    if len(text) > 0 and url_chars / len(text) > _MAX_LINK_DENSITY:
        return _reject(fetch_result, f"link density too high ({url_chars}/{len(text)})")

    # 4. SEO phrase heuristics
    lower = text.lower()
    for phrase in _SEO_PHRASES:
        if phrase in lower:
            return _reject(fetch_result, f"SEO/noise phrase detected: {phrase!r}")

    return fetch_result


def _reject(fetch_result: FetchResult, reason: str) -> FetchResult:
    fetch_result.rejected = True
    fetch_result.rejected_reason = reason
    return fetch_result
