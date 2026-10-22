"""
Historical backfill: archive reading, the daily draw, and backfill extraction.

docs/augur-architecture.md, "Historical backfill".  The Wayback snapshot is a
real one (Al Jazeera's feed as archived at 2026-03-10 00:03 UTC); the lens
output replayed is a real Nemotron Super answer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from augur.backfill.selection import draw_day
from augur.backfill.wayback import items_published_on, pick_snapshots
from augur.ingestion.fetchers.rss import parse_feed_entries
from augur.ingestion.models import FetchResult, SourceConfig

FIXTURES = Path(__file__).parent / "fixtures"
SNAPSHOT = FIXTURES / "wayback_aljazeera_20260310000333.xml"


def _source(source_id: str = "aljazeera_rss") -> SourceConfig:
    return SourceConfig(
        source_id=source_id, canonical_name=source_id, url_base="https://example.com",
        tier="2", perspective="gulf", languages=["en"], access_method="rss",
        access_config={}, update_cadence="hourly", domains=[], starting_source_weight=0.7,
    )


def _item(source_id: str, n: int) -> FetchResult:
    return FetchResult(
        source_id=source_id, url=f"https://{source_id}/{n}", perspective="x",
        raw_content=f"{source_id} item {n}", fetched_at=datetime.now(UTC),
        content_timestamp=datetime(2026, 3, 9, 12, tzinfo=UTC), content_type="article",
        language="en", metadata={},
    )


# ── The daily draw ───────────────────────────────────────────────────────────


def test_draw_is_round_robin_within_caps() -> None:
    by_source = {
        "aljazeera_rss": [_item("aljazeera_rss", i) for i in range(20)],
        "tass_rss": [_item("tass_rss", i) for i in range(3)],
        "bloomberg_rss": [_item("bloomberg_rss", i) for i in range(5)],
    }
    drawn = draw_day(by_source, per_day=25, per_source_cap=8)

    counts = {s: sum(1 for d in drawn if d.source_id == s) for s in by_source}
    assert counts == {"aljazeera_rss": 8, "tass_rss": 3, "bloomberg_rss": 5}
    # Round-robin: the first three drawn come from three different sources
    assert len({d.source_id for d in drawn[:3]}) == 3
    # A feed's own order is kept
    assert [d.url for d in drawn if d.source_id == "tass_rss"] == [
        "https://tass_rss/0", "https://tass_rss/1", "https://tass_rss/2"]


def test_draw_stops_at_the_daily_limit() -> None:
    by_source = {f"s{i}": [_item(f"s{i}", j) for j in range(10)] for i in range(5)}
    assert len(draw_day(by_source, per_day=25, per_source_cap=8)) == 25


def test_draw_of_an_empty_day_is_empty() -> None:
    assert draw_day({"a": []}, per_day=25, per_source_cap=8) == []


# ── Reading the archive ──────────────────────────────────────────────────────


def test_snapshots_nearest_the_target_hours_are_picked() -> None:
    timestamps = [f"20260309{h:02d}0500" for h in range(24)] + ["20260310000333"]
    picked = pick_snapshots(timestamps, date(2026, 3, 9))
    assert picked == ["20260309060500", "20260309120500", "20260309180500", "20260310000333"]


def test_a_day_without_snapshots_picks_none() -> None:
    assert pick_snapshots([], date(2026, 3, 9)) == []


def test_real_snapshot_items_are_kept_only_for_their_day() -> None:
    results = parse_feed_entries(
        SNAPSHOT.read_text(encoding="utf-8"), source=_source(),
        feed_url="https://www.aljazeera.com/xml/rss/all.xml", label="all",
        fetched_at=datetime.now(UTC),
    )
    assert len(results) == 25, "the archived feed carries 25 items"

    on_the_9th = items_published_on(results, date(2026, 3, 9))
    assert on_the_9th, "the snapshot taken just after midnight holds the 9th's items"
    assert all(r.content_timestamp.date() == date(2026, 3, 9) for r in on_the_9th)
    assert all(r.raw_content.strip() for r in on_the_9th)
    assert items_published_on(results, date(2026, 3, 1)) == []


def test_duplicate_items_across_snapshots_are_kept_once() -> None:
    results = parse_feed_entries(
        SNAPSHOT.read_text(encoding="utf-8"), source=_source(),
        feed_url="https://www.aljazeera.com/xml/rss/all.xml", label="all",
        fetched_at=datetime.now(UTC),
    )
    once = items_published_on(results, date(2026, 3, 9))
    twice = items_published_on(results + results, date(2026, 3, 9))
    assert len(twice) == len(once)


# ── Backfill extraction ──────────────────────────────────────────────────────


class _Recorded:
    """Replays a recorded lens output and keeps what it was asked."""

    def __init__(self) -> None:
        self.content = (FIXTURES / "financial_lens_nemotron_super.json").read_text(encoding="utf-8")
        self.requests: list[dict] = []

    async def complete(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(content=self.content, model="inclusionai/ling-3.0-flash",
                               call_id="c", prompt_tokens=0, completion_tokens=0)


async def _extract(origin: str):
    from augur.extraction.executor import LensExecutor
    from augur.extraction.lenses import ACTIVE_LENSES

    llm = _Recorded()
    lens = next(l for l in ACTIVE_LENSES if l.lens_id == "financial")
    signals = await LensExecutor(llm, origin=origin).extract(
        payload_id=uuid4(), content="USD/JPY rose 0.32% over five days.",
        content_timestamp=datetime(2026, 3, 9, 14, tzinfo=UTC), source_id="yahoo_finance",
        lens=lens,
    )
    return llm.requests[0], lens, signals


@pytest.mark.asyncio
async def test_backfill_extraction_is_sandboxed_to_the_day_in_the_user_message() -> None:
    request, lens, signals = await _extract("backfill")
    system, user = request["messages"]
    assert system["content"] == lens.system_prompt, "system prompt unchanged, so cacheable"
    assert user["content"].startswith("The current date is 09 March 2026.")
    assert request["metadata"]["backfill"] is True
    assert signals and all(s["origin"] == "backfill" for s in signals)


@pytest.mark.asyncio
async def test_live_extraction_has_no_date_note_or_backfill_flag() -> None:
    request, _, _ = await _extract("live")
    assert request["messages"][1]["content"].startswith("<payload")
    assert "backfill" not in request["metadata"]
