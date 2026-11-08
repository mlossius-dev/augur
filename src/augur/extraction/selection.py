"""
Which payloads extraction reads (docs/augur-signal-pipeline.md, "Which
payloads are read").

The free tier lets extraction read a few payloads an hour out of hundreds.
`plan_run` orders the 24-hour queue from the triage answers (augur.extraction.
triage): soft news dropped, a story read once a day, consequence first with
breadth breaking ties, a fifth of the calls reserved for slow-building risks,
and each payload costing only the lenses relevant to it. Breadth is how many
other sources carry the same story, the story being the distinctive words of
a payload's headline. Payloads without triage answers come after the triaged
ones, in breadth order, read by every lens. No LLM call: ordering only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

_WORD = re.compile(r"[a-z][a-z0-9'-]+")
# Words that say nothing about which story a headline is
_STOP = frozenset("""
a about above after again against all also am an and any are as at be because been before
being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers him his how i if in into is it its itself just
me more most my no nor not now of off on once only or other our out over own same she should
so some such than that the their them then there these they this those through to too under
until up very was we were what when where which while who whom why will with would you your
says said say new news report reports update live latest year years day days week weeks
amid after over says new first last more after could would may might
""".split())

# Shared distinctive words for two headlines to count as one story
STORY_OVERLAP = 2


def story_words(text: str) -> frozenset[str]:
    """The distinctive words of a headline: lower-cased, stop words and short words dropped."""
    headline = (text or "").strip().splitlines()[0] if text else ""
    return frozenset(w for w in _WORD.findall(headline.lower()) if w not in _STOP and len(w) > 3)


@dataclass
class Candidate:
    payload_id: Any
    source_id: str
    content: str
    content_timestamp: datetime
    source_weight: float = 0.5
    words: frozenset[str] = field(default_factory=frozenset)
    breadth: int = 0  # other sources carrying the same story
    triage: dict[str, float] | None = None  # question -> probability; None when not triaged
    lenses: list[str] = field(default_factory=list)  # the lenses that will read it
    score: float = 0.0  # consequence (+ radar boost); -1 when not triaged


def rank_by_breadth(candidates: list[Candidate]) -> list[Candidate]:
    """Score every candidate's breadth and sort: widest story, then weight, then newest."""
    for c in candidates:
        c.words = story_words(c.content)
    for c in candidates:
        if len(c.words) < STORY_OVERLAP:
            c.breadth = 0
            continue
        c.breadth = len({
            o.source_id for o in candidates
            if o.source_id != c.source_id and len(c.words & o.words) >= STORY_OVERLAP
        })
    return sorted(
        candidates,
        key=lambda c: (c.breadth, c.source_weight, c.content_timestamp),
        reverse=True,
    )


def pick_for_run(candidates: list[Candidate], n: int) -> list[Candidate]:
    """The top `n` by breadth, at most one per story."""
    picked: list[Candidate] = []
    for c in rank_by_breadth(candidates):
        if len(picked) >= n:
            break
        if any(len(c.words & p.words) >= STORY_OVERLAP for p in picked):
            continue  # this story already has its payload this run
        picked.append(c)
    return picked


# docs/augur-signal-pipeline.md, "Which payloads are read" (November 2026)
SOFT_DROP = 0.5          # soft news at or above this is never read
SLOW_RISK_MIN = 0.7      # a slow-building risk for the reserve
SLOW_SHARE = 0.2         # share of a run's calls reserved for them
RADAR_BOOST = 0.15       # consequence boost in a domain the radar flags
RADAR_LENS_MIN = 0.5     # how relevant that domain's lens must be


def _same_story(c: Candidate, words: frozenset[str]) -> bool:
    return len(c.words) >= STORY_OVERLAP and len(c.words & words) >= STORY_OVERLAP


def plan_run(
    candidates: list[Candidate],
    *,
    call_budget: int,
    lens_ids: list[str],
    read_stories: list[frozenset[str]] = (),  # type: ignore[assignment]
    radar_lenses: set[str] = frozenset(),  # type: ignore[assignment]
) -> list[Candidate]:
    """
    The payloads one run reads, each with the lenses that will read it.

    `call_budget` is the run's lens calls; a payload costs one per lens.
    `read_stories` are the story words of payloads read in the last day;
    `radar_lenses` the lenses whose domain the radar flags.
    """
    from augur.extraction.triage import relevant_lenses

    eligible: list[Candidate] = []
    for c in rank_by_breadth(candidates):
        t = c.triage
        if t and t.get("soft", 0.0) >= SOFT_DROP:
            continue
        if any(_same_story(c, w) for w in read_stories):
            continue  # this story was read in the last day
        c.lenses = relevant_lenses(t, lens_ids)
        if not c.lenses:
            continue
        if t:
            boosted = any(t.get(f"lens_{lens}", 0.0) >= RADAR_LENS_MIN for lens in radar_lenses)
            c.score = t.get("consequence", 0.0) + (RADAR_BOOST if boosted else 0.0)
        else:
            c.score = -1.0
        eligible.append(c)

    main = sorted(eligible, key=lambda c: (c.score, c.breadth, c.source_weight, c.content_timestamp),
                  reverse=True)
    slow = sorted((c for c in eligible if c.triage and c.triage.get("slow_risk", 0.0) >= SLOW_RISK_MIN),
                  key=lambda c: (c.triage["slow_risk"], c.score, c.breadth), reverse=True)

    picked: list[Candidate] = []
    used = 0

    def take(c: Candidate, limit: int) -> None:
        nonlocal used
        if c in picked or used + len(c.lenses) > limit:
            return
        if any(_same_story(c, p.words) for p in picked):
            return  # one payload per story per run
        picked.append(c)
        used += len(c.lenses)

    for c in slow:
        take(c, int(call_budget * SLOW_SHARE))
    for c in main:
        take(c, call_budget)
    if not picked and main:
        picked.append(main[0])  # at least one, even past the budget
    return picked

