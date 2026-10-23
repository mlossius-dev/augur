"""
Which payloads extraction reads (docs/augur-signal-pipeline.md, "Which
payloads are read").

The free tier lets extraction read a few payloads an hour out of hundreds.
This ranks the 24-hour queue by breadth: how many other sources carry the
same story, the story being the distinctive words of a payload's headline.
Source weight breaks ties, then recency; one payload per story per run.
No LLM call: ranking only.
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
