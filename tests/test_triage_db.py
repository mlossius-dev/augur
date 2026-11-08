"""
triage_pending against a real database (APPLIER_TEST_DATABASE_URL).

The decision answers are the recorded Jev response
(tests/fixtures/jev_triage_junk_debt.json), replayed by a stand-in client;
the database side (which payloads are triaged, what is stored) is real.
"""

from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from pathlib import Path

import pytest

from augur.extraction.triage import QUESTIONS, triage_pending
from augur.llm.client import DecisionResponse, LLMBudgetExceededError

DSN = os.environ.get("APPLIER_TEST_DATABASE_URL", "")
db = pytest.mark.skipif(not DSN, reason="APPLIER_TEST_DATABASE_URL not set")
RECORDED = json.loads((Path(__file__).parent / "fixtures" / "jev_triage_junk_debt.json").read_text(encoding="utf-8"))


class _Replay:
    """Answers every decision with the recorded response; can run out of budget."""

    def __init__(self, budget_calls: int = 1000) -> None:
        self.calls: list[dict] = []
        self.budget_calls = budget_calls

    async def decide(self, **kwargs) -> DecisionResponse:
        if len(self.calls) >= self.budget_calls:
            raise LLMBudgetExceededError("budget")
        self.calls.append(kwargs)
        r = RECORDED["response"]
        return DecisionResponse(answers=r["answers"], model=r.get("model") or "typesafe/jev-1.13",
                                cost_usd=Decimal(str(r["usage"]["cost"])), call_id=str(uuid.uuid4()))


@pytest.fixture
async def pool():
    import asyncpg

    p = await asyncpg.create_pool(DSN.replace("postgresql+asyncpg://", "postgresql://"),
                                  server_settings={"search_path": 'ag_catalog, "$user", public'},
                                  min_size=1, max_size=4)
    tag = f"triage-test-{uuid.uuid4().hex[:8]}"
    ids: list[uuid.UUID] = []

    async def payload(source: str = tag, *, hours_ago: int = 1, rejected: bool = False,
                      backfill: bool = False) -> uuid.UUID:
        pid = await p.fetchval(
            """
            INSERT INTO payloads (source_id, fetched_at, content_timestamp, perspective, content, metadata, rejected)
            VALUES ($1, now() - make_interval(hours => $2), now() - make_interval(hours => $2), 'us_eu', $3,
                    $4::jsonb, $5)
            RETURNING payload_id
            """,
            source, hours_ago, f"Junk debt rout {uuid.uuid4().hex}\n\nbody",
            json.dumps({"backfill": True} if backfill else {}), rejected,
        )
        ids.append(pid)
        return pid

    yield p, payload, tag, ids
    await p.execute("DELETE FROM payload_triage WHERE payload_id = ANY($1)", ids)
    await p.execute("DELETE FROM payloads WHERE payload_id = ANY($1)", ids)
    await p.close()


async def _triaged(p, ids) -> dict:
    rows = await p.fetch("SELECT payload_id, answers, model FROM payload_triage WHERE payload_id = ANY($1)", ids)
    return {r["payload_id"]: r for r in rows}


@db
@pytest.mark.asyncio
async def test_live_news_of_the_last_day_is_triaged_once(pool) -> None:
    p, payload, tag, ids = pool
    fresh = await payload()
    llm = _Replay()
    await triage_pending(p, llm, limit=10_000, exclude_sources=("yahoo_finance", "fred"))
    stored = await _triaged(p, ids)
    assert fresh in stored
    answers = json.loads(stored[fresh]["answers"])
    assert answers["lens_financial"] > 0.9 and set(answers) == set(QUESTIONS)
    asked = [c for c in llm.calls if c["metadata"]["payload_id"] == str(fresh)]
    assert len(asked) == 1 and asked[0]["state"]["source"] == tag

    llm2 = _Replay()
    await triage_pending(p, llm2, limit=10_000, exclude_sources=("yahoo_finance", "fred"))
    assert not [c for c in llm2.calls if c["metadata"]["payload_id"] == str(fresh)], "not asked twice"


@db
@pytest.mark.asyncio
async def test_old_rejected_backfill_and_market_payloads_are_not_triaged(pool) -> None:
    p, payload, tag, ids = pool
    skipped = [await payload(hours_ago=30), await payload(rejected=True), await payload(backfill=True),
               await payload("yahoo_finance")]
    await triage_pending(p, _Replay(), limit=10_000, exclude_sources=("yahoo_finance", "fred"))
    assert not set(skipped) & set(await _triaged(p, ids))


@db
@pytest.mark.asyncio
async def test_triage_stops_at_the_budget_and_leaves_the_rest_untriaged(pool) -> None:
    p, payload, tag, ids = pool
    mine = [await payload() for _ in range(3)]
    await p.execute("DELETE FROM payload_triage WHERE payload_id = ANY($1)", mine)
    llm = _Replay(budget_calls=0)
    assert await triage_pending(p, llm, limit=10_000, exclude_sources=("yahoo_finance", "fred")) == 0
    assert not set(mine) & set(await _triaged(p, ids))
