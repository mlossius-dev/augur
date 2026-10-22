"""
Where a graph change came from: signal IDs -> the articles behind them.

docs/augur-presentation.md, "Level 5 — The reasoning": every signal cites its
source, and every claim is one or two clicks from its evidence.  A signal's
payload carries the feed (source_id), headline, URL and publication time.

Graph records keep the signals of the whole anchoring batch that produced
them (`triggered_by`, `created_from`), not only those about the target, so
signals that mention the target are ranked first.  The UI labels the list
accordingly.
"""

from __future__ import annotations

import re
from typing import Any

import asyncpg

_WORD = re.compile(r"[a-z0-9]+")
# Too common to say whether a signal is about something
_STOP = {"the", "a", "an", "of", "in", "on", "to", "and", "for", "with", "by", "at", "as",
         "is", "are", "was", "from", "its", "this", "that", "over", "into", "after"}


def _words(text: str | None) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 2}


def rank_by_relevance(sources: list[dict[str, Any]], about: str | None) -> list[dict[str, Any]]:
    """Signals sharing the most words with `about` first, then newest; one per article."""
    target = _words(about)

    def overlap(s: dict[str, Any]) -> int:
        return len(target & (_words(s.get("claim_text")) | _words(s.get("headline"))))

    ranked = sorted(sources, key=lambda s: (overlap(s), s.get("published_at") or ""), reverse=True)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for s in ranked:
        key = s.get("url") or s.get("headline") or s["signal_id"]
        if key in seen:
            continue
        seen.add(key)
        unique.append({**s, "mentions_subject": overlap(s) > 0})
    return unique


async def fetch_signal_sources(
    conn: asyncpg.Connection, signal_ids: list[Any]
) -> dict[str, dict[str, Any]]:
    """signal_id -> its claim and the article it came from."""
    if not signal_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT s.signal_id::text AS signal_id, s.claim_text, s.lens_id,
               p.source_id, p.metadata->>'headline' AS headline,
               p.metadata->>'url' AS url, p.content_timestamp
        FROM signals s
        JOIN payloads p ON p.payload_id = s.payload_id
        WHERE s.signal_id = ANY($1::uuid[])
        """,
        [str(i) for i in signal_ids],
    )
    return {
        r["signal_id"]: {
            "signal_id": r["signal_id"],
            "claim_text": r["claim_text"],
            "lens_id": r["lens_id"],
            "source_id": r["source_id"],
            "headline": r["headline"],
            "url": r["url"],
            "published_at": r["content_timestamp"].isoformat() if r["content_timestamp"] else None,
        }
        for r in rows
    }
