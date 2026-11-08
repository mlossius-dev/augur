"""
Triage (docs/augur-signal-pipeline.md, "Which payloads are read"): the
decision call, its record in llm_calls, and which lenses a payload gets.

Decision answers come from a recorded Jev response
(tests/fixtures/jev_triage_junk_debt.json), replayed over httpx's mock
transport: no invented model output.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from augur.extraction.triage import (
    LENS_QUESTIONS,
    QUESTIONS,
    answers_to_scores,
    relevant_lenses,
)
from augur.llm.client import LLMBudgetExceededError, LLMCallError, LLMClient, LLMCostUnreportedError
from augur.llm.models import PipelineStage, TRIAGE_MODEL

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "jev_triage_junk_debt.json").read_text(encoding="utf-8"))
LENS_IDS = list(LENS_QUESTIONS)


def _http(*responses: httpx.Response, seen: list | None = None) -> httpx.AsyncClient:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return queue.pop(0)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _decide(client: LLMClient, http: httpx.AsyncClient):
    return await client.decide(stage=PipelineStage.TRIAGE, prompt_template_id="triage_v1",
                               state=RECORDED["request"]["state"], questions=QUESTIONS,
                               metadata={"payload_id": "p1"}, http=http)


def test_the_recorded_request_is_the_current_question_bank() -> None:
    assert RECORDED["request"]["questions"] == QUESTIONS, "re-record the fixture when the questions change"


def test_answers_become_probabilities() -> None:
    scores = answers_to_scores(RECORDED["response"]["answers"])
    assert set(scores) == set(QUESTIONS)
    assert scores["lens_financial"] > 0.9 and scores["slow_risk"] > 0.7 and scores["soft"] < 0.1


def test_relevant_lenses_follow_the_thresholds() -> None:
    scores = answers_to_scores(RECORDED["response"]["answers"])
    lenses = relevant_lenses(scores, LENS_IDS)
    assert "financial" in lenses and "narrative_divergence" in lenses and "disconfirmation" in lenses
    assert "commodities" not in lenses and "geopolitical" not in lenses


def test_financial_and_regulatory_have_the_lower_threshold() -> None:
    scores = {f"lens_{k}": 0.07 for k in LENS_IDS}
    assert set(relevant_lenses(scores, LENS_IDS)) == {"financial", "regulatory"}


def test_an_untriaged_payload_gets_every_lens() -> None:
    assert relevant_lenses(None, LENS_IDS) == LENS_IDS


@pytest.mark.asyncio
async def test_a_decision_is_recorded_before_and_after(llm_client_kwargs) -> None:
    client = LLMClient(**llm_client_kwargs)
    seen: list[httpx.Request] = []
    response = await _decide(client, _http(httpx.Response(200, json=RECORDED["response"]), seen=seen))

    assert response.answers == RECORDED["response"]["answers"]
    assert response.cost_usd == Decimal(str(RECORDED["response"]["usage"]["cost"]))
    start, end, _ = llm_client_kwargs["call_log"].calls[UUID(response.call_id)]
    assert (start.stage, start.prompt_template_id, start.model) == ("triage", "triage_v1", TRIAGE_MODEL)
    assert not start.is_free_tier and start.metadata == {"payload_id": "p1"}
    assert [m["role"] for m in start.messages] == ["state", "questions"]
    assert end.status == "ok" and end.cost_usd == response.cost_usd
    [req] = seen
    assert str(req.url) == "https://openrouter.ai/api/alpha/decisions"
    assert req.headers["authorization"] == "Bearer sk-or-v1-test-main", "the main, budgeted key"
    assert json.loads(req.content)["model"] == TRIAGE_MODEL


@pytest.mark.asyncio
async def test_no_decision_past_the_daily_budget(llm_client_kwargs) -> None:
    client = LLMClient(**{**llm_client_kwargs, "daily_budget_usd": Decimal("0")})
    with pytest.raises(LLMBudgetExceededError):
        await _decide(client, _http())
    assert llm_client_kwargs["call_log"].calls == {}, "refused before it is recorded or made"


@pytest.mark.asyncio
async def test_a_rate_limit_is_retried(llm_client_kwargs, monkeypatch) -> None:
    import augur.llm.client as mod

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)
    client = LLMClient(**llm_client_kwargs)
    response = await _decide(client, _http(httpx.Response(429, json={"error": "rate"}),
                                           httpx.Response(200, json=RECORDED["response"])))
    _, end, _ = llm_client_kwargs["call_log"].calls[UUID(response.call_id)]
    assert end.attempts == 2


@pytest.mark.asyncio
async def test_a_client_error_is_recorded_and_raised(llm_client_kwargs) -> None:
    client = LLMClient(**llm_client_kwargs)
    with pytest.raises(LLMCallError):
        await _decide(client, _http(httpx.Response(400, json={"error": {"message": "bad questions"}})))
    [(start, end, _)] = llm_client_kwargs["call_log"].calls.values()
    assert end.status == "error" and "400" in end.error


@pytest.mark.asyncio
async def test_an_unreported_cost_is_an_error(llm_client_kwargs) -> None:
    client = LLMClient(**llm_client_kwargs)
    body = {**RECORDED["response"], "usage": {}}
    with pytest.raises(LLMCostUnreportedError):
        await _decide(client, _http(httpx.Response(200, json=body)))
    [(_, end, _)] = llm_client_kwargs["call_log"].calls.values()
    assert end.status == "error"
