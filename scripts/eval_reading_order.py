"""
Experiment: in which order should extraction read the news? (operator request, November 2026)

Extraction reads about one news payload in twenty. Today it reads the most
widely reported stories first (breadth). This collects what is needed to
compare other orders:

  1. breadth for every news payload of the window, as selection computes it
     (other sources within 12 hours sharing STORY_OVERLAP headline words);
  2. Jev's answers to a question bank for every payload, read or not: one
     relevance question per lens plus consequence, novelty, slow-building
     risk and local or entertainment interest;
  3. for payloads extraction did read, what they gave the graph: nodes and
     edges created from their signals, and edges they support.

Run inside the app container:

    python scripts/eval_reading_order.py --days 7 --max-usd 1.0 --out /tmp/reading_order.json

Every Jev call is recorded in llm_calls (stage "triage", template
reading_order_eval_v1) before it is made. Nothing is written to the graph.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

import asyncpg
import httpx

from augur.extraction.selection import STORY_OVERLAP, story_words
from augur.llm.call_log import LLMCallEnd, LLMCallStart, PostgresCallLog

sys.path.insert(0, os.path.dirname(__file__))
from eval_lens_triage import LENSES  # noqa: E402

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
TEMPLATE = "reading_order_eval_v1"
MAX_CHARS = 2000
NOT_NEWS = ("yahoo_finance", "fred")

EXTRA = {
    "consequence": "The article reports a development likely to have material consequences over the coming "
    "months for the economy, financial markets, security, or the supply of energy, food or other essentials.",
    "novelty": "The article reports new information (a decision, event, data release or change), "
    "not commentary, background or a recap of known facts.",
    "slow_risk": "The article describes a building, slow-moving risk rather than a single event: for example "
    "widening credit spreads, rising defaults or debt stress, liquidity strain, falling inventories, or "
    "a shortage or imbalance that is accumulating.",
    "soft": "The article is mainly of local, celebrity, sport, entertainment or lifestyle interest.",
}
QUESTIONS = {f"lens_{k}": {"type": "noul", "instructions": v} for k, v in LENSES.items()}
QUESTIONS |= {k: {"type": "noul", "instructions": v} for k, v in EXTRA.items()}


def breadth(items: list[dict]) -> None:
    """Other sources within 12 hours sharing STORY_OVERLAP distinctive headline words."""
    index: dict[str, list[int]] = defaultdict(list)
    for i, it in enumerate(items):
        it["words"] = story_words(it["content"])
        for w in it["words"]:
            index[w].append(i)
    window = timedelta(hours=12)
    for it in items:
        shared: dict[int, int] = defaultdict(int)
        for w in it["words"]:
            for j in index[w]:
                shared[j] += 1
        it["breadth"] = len({
            items[j]["source"] for j, n in shared.items()
            if n >= STORY_OVERLAP and items[j]["source"] != it["source"]
            and abs(items[j]["fetched_at"] - it["fetched_at"]) <= window
        }) if len(it["words"]) >= STORY_OVERLAP else 0
        del it["words"]


async def load(pool: asyncpg.Pool, days: int) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT p.payload_id::text AS payload_id, p.source_id AS source, p.content, p.fetched_at,
               EXISTS (SELECT 1 FROM llm_calls c WHERE c.stage = 'extraction'
                         AND c.metadata->>'payload_id' = p.payload_id::text) AS read
        FROM payloads p
        WHERE p.fetched_at > now() - make_interval(days => $1)
          AND NOT p.rejected AND NOT (p.metadata ? 'backfill')
          AND p.source_id <> ALL($2::text[])
        """,
        days, list(NOT_NEWS),
    )
    items = [dict(r) for r in rows]
    # What each read payload gave the graph, through its signals
    value = await pool.fetch(
        """
        WITH s AS (SELECT payload_id::text AS pid, signal_id FROM signals
                   WHERE payload_id::text = ANY($1))
        SELECT s.pid,
               count(*) AS signals,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM signals x WHERE x.signal_id = s.signal_id AND x.anchored)) AS anchored,
               (SELECT count(*) FROM nodes n WHERE n.created_from && array_agg(s.signal_id)) AS nodes,
               (SELECT count(*) FROM edges e WHERE e.created_from && array_agg(s.signal_id)) AS edges,
               (SELECT count(*) FROM edges e WHERE e.supporting_signals && array_agg(s.signal_id)
                  AND e.current_weight_band IN ('moderate', 'strong')) AS firm_edges
        FROM s GROUP BY s.pid
        """,
        [i["payload_id"] for i in items if i["read"]],
    )
    by = {r["pid"]: dict(r) for r in value}
    for it in items:
        v = by.get(it["payload_id"], {})
        it["value"] = {k: int(v.get(k, 0)) for k in ("signals", "anchored", "nodes", "edges", "firm_edges")}
    return items


async def ask(http, log, key, it) -> None:
    state = {"source": it["source"], "article": it["content"][:MAX_CHARS]}
    call_id = uuid.uuid4()
    await log.start(LLMCallStart(
        call_id=call_id, stage="triage", prompt_template_id=TEMPLATE, model=MODEL, is_free_tier=False,
        temperature=0.0, metadata={"payload_id": it["payload_id"], "evaluation": True},
        messages=[{"role": "state", "content": json.dumps(state)},
                  {"role": "questions", "content": json.dumps(QUESTIONS)}],
    ))
    started = time.monotonic()
    try:
        resp = await http.post(DECISIONS_URL, json={"model": MODEL, "state": state, "questions": QUESTIONS},
                               headers={"Authorization": f"Bearer {key}", "X-Title": "Augur"})
        data = resp.json()
        if resp.status_code != 200 or "error" in data:
            raise RuntimeError(f"HTTP {resp.status_code}: {str(data)[:300]}")
        usage = data.get("usage") or {}
        it["jev"] = {k: float(v["noul"]) for k, v in data["answers"].items()}
        it["cost"] = float(usage.get("cost") or 0)
        await log.finish(call_id, LLMCallEnd(
            status="ok", attempts=1, latency_ms=int((time.monotonic() - started) * 1000),
            output=json.dumps(it["jev"]), prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"), cost_usd=Decimal(str(it["cost"])),
        ))
    except Exception as exc:  # noqa: BLE001 - recorded; the payload keeps no answers
        it["error"] = str(exc)[:200]
        await log.finish(call_id, LLMCallEnd(status="error", attempts=1,
                                             latency_ms=int((time.monotonic() - started) * 1000),
                                             error=str(exc)[:500]))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-usd", type=float, default=1.0)
    ap.add_argument("--out", default="/tmp/reading_order.json")
    args = ap.parse_args()

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn, server_settings={"search_path": 'ag_catalog, "$user", public'},
                                     min_size=1, max_size=8)
    items = await load(pool, args.days)
    breadth(items)
    print(f"{len(items)} news payloads, {sum(i['read'] for i in items)} read by extraction")

    log, key, spent = PostgresCallLog(pool), os.environ["OPENROUTER_API_KEY"], 0.0
    sem = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=60) as http:
        async def one(it: dict) -> None:
            nonlocal spent
            async with sem:
                if spent >= args.max_usd:
                    return
                await ask(http, log, key, it)
                spent += it.get("cost", 0.0)
        await asyncio.gather(*(one(i) for i in items))
    await pool.close()

    answered = sum("jev" in i for i in items)
    print(f"answered {answered}, errors {sum('error' in i for i in items)}, cost {spent:.4f} USD")
    for it in items:
        it["fetched_at"] = it["fetched_at"].isoformat()
        it["headline"] = (it.pop("content") or "").strip().splitlines()[0][:160]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(items, f)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
