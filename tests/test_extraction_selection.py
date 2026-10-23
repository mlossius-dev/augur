"""
Which payloads extraction reads (docs/augur-signal-pipeline.md, "Which
payloads are read"): widest-reported stories first, one per story.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from augur.extraction.selection import Candidate, pick_for_run, rank_by_breadth, story_words

T0 = datetime(2026, 10, 22, 12, tzinfo=UTC)


def _c(source: str, headline: str, minutes: int = 0, weight: float = 0.5) -> Candidate:
    return Candidate(payload_id=f"{source}:{headline}", source_id=source,
                     content=f"{headline}\n\nbody", content_timestamp=T0 + timedelta(minutes=minutes),
                     source_weight=weight)


def test_story_words_keep_only_distinctive_headline_words() -> None:
    assert story_words("Oil prices jump as Hormuz tensions rise\n\nlong body text") == frozenset(
        {"prices", "jump", "hormuz", "tensions", "rise"})


def test_a_story_many_sources_carry_outranks_a_newer_single_item() -> None:
    queue = [
        _c("ft", "Oil prices jump as Hormuz tensions rise"),
        _c("bloomberg", "Brent oil prices jump on Hormuz fears"),
        _c("aljazeera", "Hormuz tensions send oil prices higher"),
        _c("tass", "Local football club wins regional cup", minutes=50),  # newest
    ]
    ranked = rank_by_breadth(queue)
    assert ranked[0].breadth == 2 and "oil" in ranked[0].content.lower()
    assert ranked[-1].source_id == "tass" and ranked[-1].breadth == 0


def test_breadth_counts_other_sources_not_repeats_from_one_feed() -> None:
    queue = [_c("tass", f"Drone attack on Moscow region update {i}") for i in range(5)]
    assert all(c.breadth == 0 for c in rank_by_breadth(queue))


def test_one_payload_per_story_per_run() -> None:
    queue = [
        _c("ft", "Oil prices jump as Hormuz tensions rise"),
        _c("bloomberg", "Brent oil prices jump on Hormuz fears"),
        _c("aljazeera", "Hormuz tensions send oil prices higher"),
        _c("hindu", "Trump halts Ukraine weapons shipments"),
        _c("tass", "Kremlin welcomes Trump halt to Ukraine weapons"),
    ]
    picked = pick_for_run(queue, 2)
    texts = [p.content.lower() for p in picked]
    assert sum("hormuz" in t for t in texts) == 1
    assert sum("ukraine" in t for t in texts) == 1


def test_ties_break_on_source_weight_then_recency() -> None:
    queue = [_c("a", "Alpha beta gamma", minutes=0, weight=0.4),
             _c("b", "Delta epsilon zeta", minutes=5, weight=0.9),
             _c("c", "Theta iota kappa", minutes=10, weight=0.4)]
    assert [c.source_id for c in rank_by_breadth(queue)] == ["b", "c", "a"]


def test_a_run_takes_no_more_than_its_capacity() -> None:
    words = ["copper", "wheat", "tariff", "election", "drought", "satellite", "pipeline", "strike",
             "vaccine", "glacier"]
    queue = [_c(f"s{i}", f"{w.title()} {w}s dominate {i}") for i, w in enumerate(words)]
    assert len(pick_for_run(queue, 3)) == 3
