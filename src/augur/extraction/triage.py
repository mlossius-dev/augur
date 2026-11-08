"""
Triage: what a decision model says about each live news payload, before any
lens reads it (docs/augur-signal-pipeline.md, "Which payloads are read").

Jev answers a fixed question bank with calibrated probabilities: whether each
lens is relevant, whether the development is consequential, whether it
describes a slow-building risk, whether it is soft news. Selection orders the
queue from these and extraction runs only the relevant lenses. The answers
are stored in payload_triage; the calls are in llm_calls like any other.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import structlog

from augur.llm.client import LLMBudgetExceededError, LLMClient
from augur.llm.models import PipelineStage

log = structlog.get_logger(__name__)

TEMPLATE_ID = "triage_v1"
MAX_CHARS = 2000
CONCURRENCY = 8

# One question per lens (the inline disconfirmation lens included), worded as
# a statement Jev judges true or false. Evaluated in scripts/eval_lens_triage.py.
LENS_QUESTIONS = {
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
# Ordering questions, evaluated in scripts/eval_reading_order.py
ORDER_QUESTIONS = {
    "consequence": "The article reports a development likely to have material consequences over the coming "
    "months for the economy, financial markets, security, or the supply of energy, food or other essentials.",
    "slow_risk": "The article describes a building, slow-moving risk rather than a single event: for example "
    "widening credit spreads, rising defaults or debt stress, liquidity strain, falling inventories, or "
    "a shortage or imbalance that is accumulating.",
    "soft": "The article is mainly of local, celebrity, sport, entertainment or lifestyle interest.",
}
QUESTIONS: dict[str, dict[str, Any]] = {
    **{f"lens_{k}": {"type": "noul", "instructions": v} for k, v in LENS_QUESTIONS.items()},
    **{k: {"type": "noul", "instructions": v} for k, v in ORDER_QUESTIONS.items()},
}

# A lens reads a payload when Jev's probability is at least this. Lower for the
# lenses where misses were likeliest (eval: 98 % of productive calls kept).
LENS_THRESHOLD = 0.10
LENS_THRESHOLDS = {"financial": 0.05, "regulatory": 0.05}


def answers_to_scores(answers: dict[str, Any]) -> dict[str, float]:
    """Decisions API answers -> {question: probability}."""
    return {k: float(v["noul"]) for k, v in answers.items() if isinstance(v, dict) and "noul" in v}


def relevant_lenses(scores: dict[str, float] | None, lens_ids: list[str]) -> list[str]:
    """The lenses that should read a payload; all of them when it was not triaged."""
    if not scores:
        return list(lens_ids)
    return [
        lens for lens in lens_ids
        if scores.get(f"lens_{lens}", 1.0) >= LENS_THRESHOLDS.get(lens, LENS_THRESHOLD)
    ]


async def triage_pending(
    pool: asyncpg.Pool,
    llm: LLMClient,
    *,
    limit: int,
    exclude_sources: tuple[str, ...],
) -> int:
    """Triage up to `limit` untriaged live payloads of the last 24 hours, newest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.payload_id, p.source_id, p.content
            FROM payloads p
            WHERE p.fetched_at > now() - interval '24 hours'
              AND NOT p.rejected
              AND NOT (p.metadata ? 'backfill')
              AND p.source_id <> ALL($1::text[])
              AND NOT EXISTS (SELECT 1 FROM payload_triage t WHERE t.payload_id = p.payload_id)
            ORDER BY p.fetched_at DESC
            LIMIT $2
            """,
            list(exclude_sources),
            limit,
        )
    if not rows:
        return 0

    sem = asyncio.Semaphore(CONCURRENCY)
    stop = asyncio.Event()
    done = 0

    async def one(row: asyncpg.Record) -> None:
        nonlocal done
        if stop.is_set():
            return
        async with sem:
            if stop.is_set():
                return
            try:
                response = await llm.decide(
                    stage=PipelineStage.TRIAGE,
                    prompt_template_id=TEMPLATE_ID,
                    state={"source": row["source_id"], "article": (row["content"] or "")[:MAX_CHARS]},
                    questions=QUESTIONS,
                    metadata={"payload_id": str(row["payload_id"]), "source_id": row["source_id"]},
                )
            except LLMBudgetExceededError as exc:
                stop.set()
                log.warning("triage.budget_exceeded", error=str(exc))
                return
            except Exception as exc:  # recorded in llm_calls; the payload stays untriaged
                log.warning("triage.failed", payload_id=str(row["payload_id"]), error=str(exc))
                return
            scores = answers_to_scores(response.answers)
            await pool.execute(
                """
                INSERT INTO payload_triage (payload_id, model, answers, call_id)
                VALUES ($1, $2, $3::jsonb, $4::uuid)
                ON CONFLICT (payload_id) DO NOTHING
                """,
                row["payload_id"], response.model, json.dumps(scores), response.call_id,
            )
            done += 1

    await asyncio.gather(*(one(r) for r in rows))
    log.info("triage.done", n_candidates=len(rows), n_triaged=done)
    return done
