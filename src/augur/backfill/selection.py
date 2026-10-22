"""
Choosing a day's backfill payloads.

docs/augur-architecture.md, "Historical backfill": at most N payloads a day,
drawn with a per-source cap so one feed (Al Jazeera, archived nearly every
day) does not dominate the days the others are thin.  Within a source the
feed's own order is kept; across sources the draw is round-robin.
"""

from __future__ import annotations

from augur.ingestion.models import FetchResult


def draw_day(
    by_source: dict[str, list[FetchResult]], *, per_day: int, per_source_cap: int
) -> list[FetchResult]:
    """Round-robin over sources, at most `per_source_cap` each, `per_day` in all."""
    queues = {
        source_id: items[:per_source_cap]
        for source_id, items in sorted(by_source.items())
        if items
    }
    drawn: list[FetchResult] = []
    position = 0
    while len(drawn) < per_day and any(position < len(q) for q in queues.values()):
        for items in queues.values():
            if position < len(items) and len(drawn) < per_day:
                drawn.append(items[position])
        position += 1
    return drawn
