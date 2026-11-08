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


# ── plan_run: triage-driven order (November 2026) ─────────────────────────────

from augur.extraction.selection import SLOW_SHARE, plan_run  # noqa: E402

LENS_IDS = ["commodities", "financial", "geopolitical", "market", "physical_world", "regulatory",
            "narrative_divergence", "disconfirmation"]


def _t(consequence: float = 0.5, slow: float = 0.1, soft: float = 0.0, **lens: float) -> dict:
    """Triage answers; lenses not named are irrelevant (0.01)."""
    return {"consequence": consequence, "slow_risk": slow, "soft": soft,
            **{f"lens_{k}": lens.get(k, 0.01) for k in LENS_IDS}}


def _tc(source: str, headline: str, triage: dict | None, minutes: int = 0) -> Candidate:
    c = _c(source, headline, minutes)
    c.triage = triage
    return c


def test_soft_news_is_never_read() -> None:
    picked = plan_run([_tc("a", "Pop star announces world tour dates", _t(0.1, soft=0.9, geopolitical=0.5)),
                       _tc("b", "Central bank raises rates sharply", _t(0.8, financial=0.9))],
                      call_budget=20, lens_ids=LENS_IDS)
    assert [c.source_id for c in picked] == ["b"]


def test_consequence_outranks_breadth() -> None:
    loud = [_tc(s, "Midterm polling hours across states announced", _t(0.15, geopolitical=0.6)) for s in "abcde"]
    quiet = _tc("ft", "Junk debt rout sparks bloodbath for credit investors", _t(0.85, financial=0.95))
    picked = plan_run(loud + [quiet], call_budget=1, lens_ids=LENS_IDS)
    assert picked[0].source_id == "ft"


def test_a_story_read_in_the_last_day_is_not_read_again() -> None:
    crash = _tc("a", "Learjet crashes in Florida neighborhood killing nine", _t(0.3, physical_world=0.6))
    read = [story_words("Nine dead after Learjet crashes in Florida neighborhood")]
    assert plan_run([crash], call_budget=20, lens_ids=LENS_IDS, read_stories=read) == []


def test_a_payload_costs_only_its_relevant_lenses() -> None:
    items = [_tc(f"s{i}", f"{w} {w}s dominate {i}", _t(0.9 - i * 0.01, financial=0.9))
             for i, w in enumerate(["copper", "wheat", "tariff", "drought", "satellite", "pipeline"])]
    picked = plan_run(items, call_budget=6, lens_ids=LENS_IDS)
    assert all(c.lenses == ["financial"] for c in picked)
    assert len(picked) == 6, "one call each, so six fit a budget of six"


def test_a_fifth_of_the_calls_wait_for_slow_risks() -> None:
    loud = [_tc(f"l{i}", f"{w} {w}s summit {i}", _t(0.9, geopolitical=0.9))
            for i, w in enumerate(["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
                                   "india", "juliet"])]
    slow = _tc("bb", "Defaults climb as farm credit stress builds", _t(0.4, slow=0.9, financial=0.9))
    picked = plan_run(loud + [slow], call_budget=5, lens_ids=LENS_IDS)
    assert slow in picked, "below every loud story on consequence, but in the reserve"
    assert int(5 * SLOW_SHARE) == 1


def test_the_radar_raises_payloads_in_its_domain() -> None:
    geo = _tc("a", "Border talks resume between neighbours", _t(0.6, geopolitical=0.9))
    credit = _tc("b", "Leveraged loan market sees another deal pulled", _t(0.5, financial=0.8))
    assert plan_run([geo, credit], call_budget=1, lens_ids=LENS_IDS)[0].source_id == "a"
    assert plan_run([geo, credit], call_budget=1, lens_ids=LENS_IDS,
                    radar_lenses={"financial"})[0].source_id == "b"


def test_untriaged_payloads_come_after_triaged_ones_with_every_lens() -> None:
    triaged = _tc("a", "Refinery outage cuts diesel supply", _t(0.2, commodities=0.9))
    blind = _tc("b", "Untriaged wire item about markets", None)
    picked = plan_run([blind, triaged], call_budget=20, lens_ids=LENS_IDS)
    assert [c.source_id for c in picked] == ["a", "b"]
    assert picked[1].lenses == LENS_IDS


def test_one_payload_per_story_within_a_run() -> None:
    a = _tc("ft", "Oil prices jump as Hormuz tensions rise", _t(0.9, commodities=0.9))
    b = _tc("bloomberg", "Brent oil prices jump on Hormuz fears", _t(0.85, commodities=0.9))
    assert len(plan_run([a, b], call_budget=20, lens_ids=LENS_IDS)) == 1


def test_at_least_one_payload_even_past_the_budget() -> None:
    big = _tc("a", "Everything relevant to every lens", _t(0.9, **{k: 0.9 for k in LENS_IDS}))
    assert len(plan_run([big], call_budget=3, lens_ids=LENS_IDS)) == 1
