"""
Evaluate Jev as a lens triage step (operator request, November 2026).

Every payload extraction reads is read by all eight lenses, and most lenses
find nothing. This asks Jev (typesafe/jev-1.13, OpenRouter Decisions API)
one question per lens: "is this lens relevant to this article?", answered as
a calibrated probability, and compares the answers with what the lenses
actually found on the same payloads (ground truth: a lens call whose output
was a non-empty signal list).

Run inside the app container (it needs the database and OPENROUTER_API_KEY):

    python scripts/eval_lens_triage.py --days 7 --max-usd 1.0

Every call is recorded in llm_calls (stage "triage", template
lens_triage_eval_v1) before it is made, like any other LLM call. Results go to
the JSON file given by --out; nothing is written to the graph.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from decimal import Decimal

import asyncpg
import httpx

from augur.llm.call_log import LLMCallEnd, LLMCallStart, PostgresCallLog

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"
TEMPLATE = "lens_triage_eval_v1"
MAX_CHARS = 3000

LENSES = {
    "commodities": "The article reports a development in physical commodity markets or supply chains: "
    "production, exports or imports, supply and demand, plant or pipeline outages, port or shipping "
    "disruption, crop conditions, or a large price move in oil, gas, grains, fertilizer, metals or freight.",
    "financial": "The article reports on monetary policy or financial conditions: central bank actions, "
    "currencies, banking-sector stress, sovereign debt or bond markets, reserves, or large capital flows.",
    "geopolitical": "The article reports on relations between states or armed groups: conflict or military "
    "action, sanctions, diplomacy, alliances, or elections and leadership changes with international consequences.",
    "market": "The article contains an explicit market-move statement: a named financial instrument (equity "
    "index, currency pair, commodity, bond yield or VIX) together with a stated percentage change.",
    "narrative_divergence": "The article frames an event, its causes or who is responsible in a way that "
    "readers from another national or ideological perspective would likely contest.",
    "physical_world": "The article reports a physical-world event or anomaly with consequences for "
    "infrastructure, supply chains or security: earthquake, eruption, severe weather, flood, fire, "
    "shipping or aviation disruption, or infrastructure failure.",
    "regulatory": "The article reports a law, regulation, enforcement action, court ruling, trade measure "
    "(tariffs, export controls) or other policy change with material impact on markets, sectors or trade.",
    "disconfirmation": "The article reports that something previously expected, claimed or under way did "
    "not happen, was reversed, denied, failed, eased or was contradicted.",
}
QUESTIONS = {f"lens_{k}": {"type": "noul", "instructions": v} for k, v in LENSES.items()}
EMPTY = {"[]", '{"signals": []}', '{"signals":[]}'}


async def ground_truth(pool: asyncpg.Pool, days: int) -> list[dict]:
    """Payloads all eight lenses read in the window, with which lenses found signals."""
    rows = await pool.fetch(
        """
        SELECT c.metadata->>'payload_id' AS payload_id,
               regexp_replace(c.prompt_template_id, '^lens_(.*)_v[0-9]+$', '\\1') AS lens,
               trim(c.output) AS output
        FROM llm_calls c
        WHERE c.stage = 'extraction' AND c.status = 'ok'
          AND c.called_at > now() - make_interval(days => $1)
        """,
        days,
    )
    by_payload: dict[str, dict[str, bool]] = {}
    for r in rows:
        if r["lens"] in LENSES:
            by_payload.setdefault(r["payload_id"], {})[r["lens"]] = r["output"] not in EMPTY
    complete = {p: v for p, v in by_payload.items() if set(v) == set(LENSES)}
    payloads = await pool.fetch(
        "SELECT payload_id::text, source_id, content FROM payloads WHERE payload_id::text = ANY($1)",
        list(complete),
    )
    return [{"payload_id": p["payload_id"], "source": p["source_id"], "content": p["content"],
             "truth": complete[p["payload_id"]]} for p in payloads]


async def ask(http: httpx.AsyncClient, log: PostgresCallLog, key: str, item: dict) -> dict:
    state = {"source": item["source"], "article": item["content"][:MAX_CHARS]}
    call_id = uuid.uuid4()
    await log.start(LLMCallStart(
        call_id=call_id, stage="triage", prompt_template_id=TEMPLATE, model=MODEL, is_free_tier=False,
        temperature=0.0, metadata={"payload_id": item["payload_id"], "evaluation": True},
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
        probs = {k.removeprefix("lens_"): float(v["noul"]) for k, v in data["answers"].items()}
        await log.finish(call_id, LLMCallEnd(
            status="ok", attempts=1, latency_ms=int((time.monotonic() - started) * 1000),
            output=json.dumps(probs), prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"), cost_usd=Decimal(str(usage.get("cost") or 0)),
        ))
        return {**item, "probs": probs, "cost": float(usage.get("cost") or 0),
                "latency": time.monotonic() - started}
    except Exception as exc:  # noqa: BLE001 - recorded, and the item is left out
        await log.finish(call_id, LLMCallEnd(status="error", attempts=1,
                                             latency_ms=int((time.monotonic() - started) * 1000),
                                             error=str(exc)[:500]))
        return {**item, "error": str(exc)[:200]}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-usd", type=float, default=1.0)
    ap.add_argument("--limit", type=int, default=0, help="Only the first N payloads (0 = all)")
    ap.add_argument("--out", default="/tmp/lens_triage_eval.json")
    args = ap.parse_args()

    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    pool = await asyncpg.create_pool(dsn, server_settings={"search_path": 'ag_catalog, "$user", public'},
                                     min_size=1, max_size=4)
    items = await ground_truth(pool, args.days)
    if args.limit:
        items = items[: args.limit]
    print(f"{len(items)} payloads with all eight lens results")

    log, key, spent, results = PostgresCallLog(pool), os.environ["OPENROUTER_API_KEY"], 0.0, []
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=60) as http:
        async def one(item: dict) -> None:
            nonlocal spent
            async with sem:
                if spent >= args.max_usd:
                    return
                r = await ask(http, log, key, item)
                spent += r.get("cost", 0.0)
                results.append(r)
        await asyncio.gather(*(one(i) for i in items))
    await pool.close()

    ok = [r for r in results if "probs" in r]
    print(f"answered {len(ok)}, errors {len(results) - len(ok)}, cost {spent:.4f} USD")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in r.items() if k != "content"} for r in results], f)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
