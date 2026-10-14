"""
LLM client tests.

Unit tests make no real API calls: they replace the OpenAI SDK's create()
to verify call recording, budget enforcement, key routing and retries
without hitting OpenRouter.  The responses are real SDK types, built the
way OpenRouter returns them (usage.cost included).

Integration tests (marked @pytest.mark.integration) need a real API key or a
live database.  AGENTS.md prohibits mocking LLM output in pipeline tests;
integration tests use cheap real models instead.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx2
import pytest
from openai.types.chat import ChatCompletion

from augur.llm.call_log import InMemoryCallLog, LLMCallEnd, LLMCallStart
from augur.llm.models import FREE_TIER_STAGES, PipelineStage
from tests.conftest import requires_db, requires_openrouter


def _completion(content: str = "ok", *, cost: float | None = 0.0012) -> ChatCompletion:
    usage: dict = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
    if cost is not None:
        usage["cost"] = cost
    return ChatCompletion.model_validate(
        {
            "id": "gen-1",
            "object": "chat.completion",
            "created": 0,
            "model": "test/model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
            "usage": usage,
        }
    )


def _status_error(status: int) -> Exception:
    """The SDK error OpenRouter's HTTP status turns into."""
    import openai

    request = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx2.Response(status, request=request)
    return openai.APIStatusError(f"HTTP {status}", response=response, body=None)


@pytest.fixture
def client(llm_client_kwargs: dict):
    from augur.llm.client import LLMClient

    return LLMClient(**llm_client_kwargs)


def _stub(client, *, main=None, free=None) -> tuple[AsyncMock, AsyncMock]:
    main_create = main or AsyncMock(return_value=_completion())
    free_create = free or AsyncMock(return_value=_completion(cost=0.0))
    client._main_client.chat.completions.create = main_create
    client._free_client.chat.completions.create = free_create
    return main_create, free_create


async def _complete(client, stage=PipelineStage.EXTRACTION, **kwargs):
    return await client.complete(
        stage=stage,
        prompt_template_id=kwargs.pop("prompt_template_id", "test_v1"),
        messages=[{"role": "user", "content": "hello"}],
        **kwargs,
    )


# ── Unit tests ────────────────────────────────────────────────────────────────


class TestModelRouting:
    def test_default_model_routing_complete(self) -> None:
        from augur.llm.models import DEFAULT_MODEL_ROUTING

        for stage in PipelineStage:
            assert stage in DEFAULT_MODEL_ROUTING, f"No model configured for {stage}"
            assert DEFAULT_MODEL_ROUTING[stage], f"Empty model string for {stage}"

    def test_conversation_is_free_tier(self) -> None:
        assert PipelineStage.CONVERSATION in FREE_TIER_STAGES

    def test_only_conversation_and_digest_are_free_tier(self) -> None:
        # The free-tier key is for reader-facing text that must cost nothing:
        # the conversation layer and the dimension digest notes. Pipeline
        # stages that write the graph always use the budgeted key.
        assert FREE_TIER_STAGES == {PipelineStage.CONVERSATION, PipelineStage.DIGEST}

    def test_every_stage_defaults_to_a_free_model(self) -> None:
        # Operator decision (October 2026): free models only, lower quality
        # accepted. A paid default would silently start billing.
        from augur.llm.models import DEFAULT_MODEL_ROUTING, is_free_model

        for stage in PipelineStage:
            assert is_free_model(DEFAULT_MODEL_ROUTING[stage]), stage

    def test_overrides_replace_only_the_stages_they_name(self) -> None:
        from augur.llm.models import DEFAULT_MODEL_ROUTING, routing_from_overrides

        routing = routing_from_overrides(
            {PipelineStage.ANCHORING: "vendor/model:free", PipelineStage.EXTRACTION: ""}
        )
        assert routing[PipelineStage.ANCHORING] == "vendor/model:free"
        assert routing[PipelineStage.EXTRACTION] == DEFAULT_MODEL_ROUTING[PipelineStage.EXTRACTION]

    def test_overrides_are_read_from_settings(self, minimal_env, monkeypatch) -> None:
        from augur.config import get_settings
        from augur.llm.client import LLMClient

        monkeypatch.setenv("AUGUR_MODEL_DISCONFIRMATION", "vendor/strong:free")
        get_settings.cache_clear()
        try:
            client = LLMClient.from_settings(call_log=InMemoryCallLog())
        finally:
            get_settings.cache_clear()
        assert client.model_for_stage(PipelineStage.DISCONFIRMATION) == "vendor/strong:free"


class TestCallRecording:
    @pytest.mark.asyncio
    async def test_successful_call_is_recorded(self, client) -> None:
        _stub(client)
        response = await _complete(client, metadata={"payload_id": "p-1"})

        start, end, _ = client._call_log.calls[UUID(response.call_id)]
        assert start.stage == "extraction"
        assert start.prompt_template_id == "test_v1"
        assert start.metadata == {"payload_id": "p-1"}
        assert start.messages == [{"role": "user", "content": "hello"}]
        assert end.status == "ok"
        assert end.output == "ok"
        assert (end.prompt_tokens, end.completion_tokens) == (10, 5)
        assert end.cost_usd == Decimal("0.0012")
        assert end.attempts == 1
        assert response.cost_usd == Decimal("0.0012")

    @pytest.mark.asyncio
    async def test_call_is_recorded_before_openrouter_is_called(self, client) -> None:
        seen: list[int] = []

        async def create(**_kwargs):
            seen.append(len(client._call_log.calls))
            return _completion()

        _stub(client, main=AsyncMock(side_effect=create))
        await _complete(client)
        assert seen == [1]

    @pytest.mark.asyncio
    async def test_no_call_is_made_if_it_cannot_be_recorded(self, client) -> None:
        main_create, _ = _stub(client)
        client._call_log.start = AsyncMock(side_effect=RuntimeError("db down"))

        with pytest.raises(RuntimeError, match="db down"):
            await _complete(client)
        main_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_call_is_recorded_as_error(self, client, monkeypatch) -> None:
        from augur.llm import client as client_module
        from augur.llm.client import LLMCallError

        monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())
        _stub(client, main=AsyncMock(side_effect=RuntimeError("upstream 502")))

        with pytest.raises(LLMCallError):
            await _complete(client, max_retries=2)

        [(start, end, _)] = client._call_log.calls.values()
        assert end.status == "error"
        assert end.attempts == 2
        assert "upstream 502" in end.error

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [400, 401, 402, 403, 404])
    async def test_client_errors_are_not_retried(self, client, monkeypatch, status) -> None:
        # A 401 (key expired) or 402 (negative balance) fails the same way
        # every time; each retry would only spend free-tier quota.
        from augur.llm import client as client_module
        from augur.llm.client import LLMCallError

        monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())
        main_create, _ = _stub(client, main=AsyncMock(side_effect=_status_error(status)))

        with pytest.raises(LLMCallError):
            await _complete(client, max_retries=3)

        assert main_create.call_count == 1
        [(_, end, _)] = client._call_log.calls.values()
        assert end.attempts == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_transient_errors_are_retried(self, client, monkeypatch, status) -> None:
        from augur.llm import client as client_module

        monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())
        main_create, _ = _stub(
            client, main=AsyncMock(side_effect=[_status_error(status), _completion()])
        )

        response = await _complete(client, max_retries=3)

        assert main_create.call_count == 2
        _, end, _ = client._call_log.calls[UUID(response.call_id)]
        assert (end.status, end.attempts) == ("ok", 2)

    @pytest.mark.asyncio
    async def test_unreported_cost_on_budgeted_call_raises(self, client) -> None:
        from augur.llm.client import LLMCostUnreportedError

        _stub(client, main=AsyncMock(return_value=_completion(cost=None)))
        with pytest.raises(LLMCostUnreportedError):
            await _complete(client)

        [(_, end, _)] = client._call_log.calls.values()
        assert end.status == "error"
        assert end.output == "ok", "the output is still kept for inspection"

    @pytest.mark.asyncio
    async def test_unreported_cost_on_free_tier_call_is_accepted(self, client) -> None:
        _stub(client, free=AsyncMock(return_value=_completion(cost=None)))
        response = await _complete(client, stage=PipelineStage.CONVERSATION)
        assert response.cost_usd == Decimal(0)

    @pytest.mark.asyncio
    async def test_request_asks_for_cost_and_carries_trace(self, client) -> None:
        main_create, _ = _stub(client)
        response = await _complete(
            client, prompt_template_id="lens_v2", metadata={"signal_ids": [uuid4()]}
        )

        extra = main_create.call_args.kwargs["extra_body"]
        assert extra["usage"] == {"include": True}
        assert extra["trace"]["trace_id"] == response.call_id
        assert extra["trace"]["trace_name"] == "extraction.lens_v2"
        assert extra["trace"]["prompt_template_id"] == "lens_v2"
        assert isinstance(extra["trace"]["signal_ids"], str), "non-scalars are stringified"

    @pytest.mark.asyncio
    async def test_the_model_that_answered_is_recorded(self, client) -> None:
        answered = _completion()
        answered.model = "vendor/picked-by-router:free"
        _stub(client, main=AsyncMock(return_value=answered))

        response = await _complete(client)

        start, end, _ = client._call_log.calls[UUID(response.call_id)]
        assert start.model == "openrouter/free"
        assert end.model == response.model == "vendor/picked-by-router:free"

    def test_sdk_retries_are_disabled(self, client) -> None:
        assert client._main_client.max_retries == 0
        assert client._free_client.max_retries == 0


class TestLLMClientBudget:
    @pytest.mark.asyncio
    async def test_budget_exceeded_raises_without_calling(self, client) -> None:
        from augur.llm.client import LLMBudgetExceededError

        main_create, _ = _stub(client, main=AsyncMock(return_value=_completion(cost=5.0)))
        await _complete(client)

        with pytest.raises(LLMBudgetExceededError):
            await _complete(client)
        assert main_create.call_count == 1

    @pytest.mark.asyncio
    async def test_spend_accumulates_from_reported_costs(self, client) -> None:
        _stub(client, main=AsyncMock(return_value=_completion(cost=1.25)))
        await _complete(client)
        await _complete(client)
        today = datetime.now(UTC).date()
        assert await client._call_log.budgeted_spend_for(today) == Decimal("2.50")

    @pytest.mark.asyncio
    async def test_budget_does_not_apply_to_conversation(self, client) -> None:
        """Conversation uses the free-tier key; the budget cap is irrelevant."""
        _stub(client, main=AsyncMock(return_value=_completion(cost=5.0)))
        await _complete(client)  # exhausts the budget

        result = await _complete(client, stage=PipelineStage.CONVERSATION)
        assert result.content == "ok"

    @pytest.mark.asyncio
    async def test_free_tier_cost_does_not_count(self, client) -> None:
        _stub(client, free=AsyncMock(return_value=_completion(cost=3.0)))
        await _complete(client, stage=PipelineStage.CONVERSATION)
        today = datetime.now(UTC).date()
        assert await client._call_log.budgeted_spend_for(today) == Decimal(0)

    def test_model_for_stage(self, client) -> None:
        for stage in PipelineStage:
            model = client.model_for_stage(stage)
            assert isinstance(model, str) and model


class TestCallQuota:
    @pytest.fixture
    def quota_client(self, llm_client_kwargs: dict):
        from augur.llm.client import LLMClient

        return LLMClient(**llm_client_kwargs, daily_call_limit=2, calls_per_minute=100)

    @pytest.mark.asyncio
    async def test_daily_quota_refuses_without_calling(self, quota_client) -> None:
        from augur.llm.client import LLMQuotaExceededError

        main_create, _ = _stub(quota_client)
        await _complete(quota_client)
        await _complete(quota_client)

        with pytest.raises(LLMQuotaExceededError):
            await _complete(quota_client)
        assert main_create.call_count == 2
        assert len(quota_client._call_log.calls) == 2

    @pytest.mark.asyncio
    async def test_failed_calls_count_against_the_quota(self, quota_client, monkeypatch) -> None:
        from augur.llm import client as client_module
        from augur.llm.client import LLMCallError, LLMQuotaExceededError

        monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())
        _stub(quota_client, main=AsyncMock(side_effect=RuntimeError("upstream 429")))
        for _ in range(2):
            with pytest.raises(LLMCallError):
                await _complete(quota_client, max_retries=1)
        with pytest.raises(LLMQuotaExceededError):
            await _complete(quota_client, max_retries=1)

    @pytest.mark.asyncio
    async def test_every_attempt_counts_against_the_quota(
        self, quota_client, monkeypatch
    ) -> None:
        # OpenRouter counts requests, not logical calls: one call that took
        # two attempts uses the whole quota of 2.
        from augur.llm import client as client_module
        from augur.llm.client import LLMQuotaExceededError

        monkeypatch.setattr(client_module.asyncio, "sleep", AsyncMock())
        main_create, _ = _stub(
            quota_client, main=AsyncMock(side_effect=[_status_error(503), _completion()])
        )
        await _complete(quota_client, max_retries=2)

        with pytest.raises(LLMQuotaExceededError):
            await _complete(quota_client)
        assert main_create.call_count == 2

    @pytest.mark.asyncio
    async def test_quota_applies_to_free_tier_stages_too(self, quota_client) -> None:
        from augur.llm.client import LLMQuotaExceededError

        _stub(quota_client)
        await _complete(quota_client, stage=PipelineStage.CONVERSATION)
        await _complete(quota_client, stage=PipelineStage.DIGEST)
        with pytest.raises(LLMQuotaExceededError):
            await _complete(quota_client, stage=PipelineStage.CONVERSATION)

    @pytest.mark.asyncio
    async def test_per_minute_rate_waits_for_a_free_slot(self, llm_client_kwargs, monkeypatch) -> None:
        from augur.llm import client as client_module
        from augur.llm.client import LLMClient

        client = LLMClient(**llm_client_kwargs, calls_per_minute=1)
        main_create, _ = _stub(client)
        await _complete(client)

        waits: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            waits.append(seconds)
            # The minute passes: age the first call out of the window
            for call_id, (start, end, called_at) in list(client._call_log.calls.items()):
                client._call_log.calls[call_id] = (start, end, called_at - timedelta(minutes=2))

        monkeypatch.setattr(client_module.asyncio, "sleep", fake_sleep)
        await _complete(client)

        assert waits, "the second call should have waited"
        assert main_create.call_count == 2

    @pytest.mark.asyncio
    async def test_health_check_reports_calls_today(self, quota_client) -> None:
        _stub(quota_client)
        await _complete(quota_client)
        result = await quota_client.health_check()
        assert result["today_calls"] == 1
        assert result["daily_call_limit"] == 2


class TestLLMClientKeyRouting:
    """Verify that conversation stage uses the free-tier client."""

    @pytest.mark.asyncio
    async def test_conversation_uses_free_client(self, client) -> None:
        main_create, free_create = _stub(client)
        await _complete(client, stage=PipelineStage.CONVERSATION)
        free_create.assert_called_once()
        main_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_extraction_uses_main_client(self, client) -> None:
        main_create, free_create = _stub(client)
        await _complete(client, stage=PipelineStage.EXTRACTION)
        main_create.assert_called_once()
        free_create.assert_not_called()


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_reports_spend_and_keys(self, client) -> None:
        result = await client.health_check()
        assert result["call_log_available"] is True
        assert result["main_key_configured"] is True
        assert result["today_spend_usd"] == "0"


@pytest.mark.integration
@requires_db()
class TestPostgresCallLog:
    """Round-trip through llm_calls on a migrated database (DATABASE_URL)."""

    @pytest.mark.asyncio
    async def test_start_finish_and_spend(self) -> None:
        import asyncpg

        from augur.llm.call_log import PostgresCallLog

        dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
        pool = await asyncpg.create_pool(
            dsn, server_settings={"search_path": 'ag_catalog, "$user", public'}
        )
        log = PostgresCallLog(pool)
        call_id = uuid4()
        today = datetime.now(UTC).date()
        try:
            before = await log.budgeted_spend_for(today)
            await log.start(
                LLMCallStart(
                    call_id=call_id,
                    stage="extraction",
                    prompt_template_id="test_v1",
                    model="test/model",
                    is_free_tier=False,
                    temperature=0.1,
                    metadata={"signal_id": uuid4()},
                    messages=[{"role": "user", "content": "hi"}],
                )
            )
            await log.finish(
                call_id,
                LLMCallEnd(
                    status="ok", attempts=1, latency_ms=42, output="hello",
                    prompt_tokens=3, completion_tokens=1, cost_usd=Decimal("0.5"),
                    model="vendor/answered:free",
                ),
            )
            row = await pool.fetchrow("SELECT * FROM llm_calls WHERE call_id = $1", call_id)
            assert row["status"] == "ok"
            assert row["finished_at"] is not None
            assert row["model"] == "vendor/answered:free"
            assert await log.calls_since(row["called_at"]) >= 1
            assert json.loads(row["messages"]) == [{"role": "user", "content": "hi"}]
            assert await log.budgeted_spend_for(today) == before + Decimal("0.5")
        finally:
            await pool.execute("DELETE FROM llm_calls WHERE call_id = $1", call_id)
            await pool.close()


# ── Integration tests ─────────────────────────────────────────────────────────


@pytest.mark.integration
@requires_openrouter()
class TestLLMIntegration:
    """
    Real API call tests.  Use the cheapest extraction model (Gemini Flash)
    and verify the call is recorded with its cost.

    Per AGENTS.md: no mocked LLM calls — use real cheap models.
    """

    @pytest.mark.asyncio
    async def test_extraction_call_produces_trace(self) -> None:
        from augur.config import get_settings
        from augur.llm.client import LLMClient

        get_settings.cache_clear()
        call_log = InMemoryCallLog()
        client = LLMClient.from_settings(call_log=call_log)

        response = await client.complete(
            stage=PipelineStage.EXTRACTION,
            prompt_template_id="integration_test_v1",
            messages=[
                {
                    "role": "user",
                    "content": "Reply with the single word 'AUGUR' and nothing else.",
                }
            ],
            metadata={"test": True},
        )

        assert "AUGUR" in response.content.upper()
        assert response.prompt_tokens > 0
        assert response.completion_tokens > 0
        _, end, _ = call_log.calls[UUID(response.call_id)]
        assert end is not None and end.status == "ok"
        assert end.cost_usd is not None


class TestExtractionCap:
    def test_keeps_as_many_newest_payloads_as_fit(self) -> None:
        from augur.scheduler import cap_extraction_payloads

        rows = ["newest", "newer", "older", "oldest"]
        assert cap_extraction_payloads(rows, calls_per_run=20, calls_per_payload=8) == [
            "newest",
            "newer",
        ]

    def test_always_processes_at_least_one(self) -> None:
        from augur.scheduler import cap_extraction_payloads

        assert cap_extraction_payloads(["only"], calls_per_run=3, calls_per_payload=8) == ["only"]
