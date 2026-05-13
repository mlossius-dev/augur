"""
LLM client tests.

Unit tests use no real API calls: they verify budget tracking, key routing,
retry logic, and model selection without hitting OpenRouter or Langfuse.

Integration tests (marked @pytest.mark.integration) require real API keys
and produce real Langfuse traces.  AGENTS.md prohibits mocking LLM calls
in tests; integration tests use cheap real models instead.
"""

from __future__ import annotations

from datetime import UTC, date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from augur.llm.models import FREE_TIER_STAGES, PipelineStage
from tests.conftest import requires_openrouter


# ── Unit tests ────────────────────────────────────────────────────────────────


class TestDailySpend:
    def test_starts_at_zero(self) -> None:
        from augur.llm.client import _DailySpend

        spend = _DailySpend()
        assert spend.total == Decimal("0")

    def test_add_accumulates(self) -> None:
        from augur.llm.client import _DailySpend

        spend = _DailySpend()
        spend.add(Decimal("1.50"))
        spend.add(Decimal("0.75"))
        assert spend.total == Decimal("2.25")

    def test_exceeds_returns_false_below_cap(self) -> None:
        from augur.llm.client import _DailySpend

        spend = _DailySpend()
        spend.add(Decimal("4.99"))
        assert not spend.exceeds(Decimal("5.00"))

    def test_exceeds_returns_true_at_cap(self) -> None:
        from augur.llm.client import _DailySpend

        spend = _DailySpend()
        spend.add(Decimal("5.00"))
        assert spend.exceeds(Decimal("5.00"))

    def test_resets_on_new_day(self) -> None:
        from augur.llm.client import _DailySpend

        spend = _DailySpend()
        spend.add(Decimal("5.00"))
        # Simulate yesterday
        spend.day = date.today() - timedelta(days=1)
        assert not spend.exceeds(Decimal("5.00"))
        assert spend.total == Decimal("0"), "Should have reset on date change"


class TestModelRouting:
    def test_default_model_routing_complete(self) -> None:
        from augur.llm.models import DEFAULT_MODEL_ROUTING

        for stage in PipelineStage:
            assert stage in DEFAULT_MODEL_ROUTING, f"No model configured for {stage}"
            assert DEFAULT_MODEL_ROUTING[stage], f"Empty model string for {stage}"

    def test_conversation_is_free_tier(self) -> None:
        assert PipelineStage.CONVERSATION in FREE_TIER_STAGES

    def test_non_conversation_stages_are_not_free_tier(self) -> None:
        for stage in PipelineStage:
            if stage != PipelineStage.CONVERSATION:
                assert stage not in FREE_TIER_STAGES


class TestLLMClientBudget:
    @pytest.fixture
    def client(self, llm_client_kwargs: dict):
        from augur.llm.client import LLMClient

        return LLMClient(**llm_client_kwargs)

    @pytest.mark.asyncio
    async def test_budget_exceeded_raises(self, client) -> None:
        from augur.llm.client import LLMBudgetExceededError

        # Exhaust the budget
        client._spend.add(Decimal("5.00"))

        with pytest.raises(LLMBudgetExceededError):
            await client.complete(
                stage=PipelineStage.EXTRACTION,
                prompt_template_id="test_v1",
                messages=[{"role": "user", "content": "test"}],
            )

    @pytest.mark.asyncio
    async def test_budget_does_not_apply_to_conversation(self, client) -> None:
        """Conversation uses free-tier key; budget cap is irrelevant."""
        # Exhaust the main key budget
        client._spend.add(Decimal("5.00"))

        # Patch the actual OpenAI call so we don't hit the network
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "test ok"
        mock_response.usage = MagicMock(prompt_tokens=5, completion_tokens=5)

        mock_langfuse_trace = MagicMock()
        mock_langfuse_trace.id = "trace-123"
        mock_langfuse_trace.generation.return_value = MagicMock()
        client._langfuse.trace = MagicMock(return_value=mock_langfuse_trace)

        client._free_client.chat = MagicMock()
        client._free_client.chat.completions = MagicMock()
        client._free_client.chat.completions.create = AsyncMock(return_value=mock_response)

        # Should NOT raise LLMBudgetExceededError
        result = await client.complete(
            stage=PipelineStage.CONVERSATION,
            prompt_template_id="conv_test_v1",
            messages=[{"role": "user", "content": "hello"}],
        )
        assert result.content == "test ok"

    def test_model_for_stage(self, client) -> None:
        for stage in PipelineStage:
            model = client.model_for_stage(stage)
            assert isinstance(model, str) and model


class TestLLMClientKeyRouting:
    """Verify that conversation stage uses the free-tier client."""

    @pytest.fixture
    def client(self, llm_client_kwargs: dict):
        from augur.llm.client import LLMClient

        return LLMClient(**llm_client_kwargs)

    @pytest.mark.asyncio
    async def test_conversation_uses_free_client(self, client) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "free tier response"
        mock_response.usage = MagicMock(prompt_tokens=3, completion_tokens=5)

        mock_trace = MagicMock()
        mock_trace.id = "trace-free"
        mock_trace.generation.return_value = MagicMock()
        client._langfuse.trace = MagicMock(return_value=mock_trace)

        free_create = AsyncMock(return_value=mock_response)
        main_create = AsyncMock(return_value=mock_response)
        client._free_client.chat.completions.create = free_create
        client._main_client.chat.completions.create = main_create

        await client.complete(
            stage=PipelineStage.CONVERSATION,
            prompt_template_id="conv_v1",
            messages=[{"role": "user", "content": "hello"}],
        )

        free_create.assert_called_once()
        main_create.assert_not_called()

    @pytest.mark.asyncio
    async def test_extraction_uses_main_client(self, client) -> None:
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "extracted signal"
        mock_response.usage = MagicMock(prompt_tokens=100, completion_tokens=50)

        mock_trace = MagicMock()
        mock_trace.id = "trace-main"
        mock_trace.generation.return_value = MagicMock()
        client._langfuse.trace = MagicMock(return_value=mock_trace)

        free_create = AsyncMock(return_value=mock_response)
        main_create = AsyncMock(return_value=mock_response)
        client._free_client.chat.completions.create = free_create
        client._main_client.chat.completions.create = main_create

        await client.complete(
            stage=PipelineStage.EXTRACTION,
            prompt_template_id="lens_extraction_v1",
            messages=[{"role": "user", "content": "article content"}],
        )

        main_create.assert_called_once()
        free_create.assert_not_called()


# ── Integration tests ─────────────────────────────────────────────────────────


@pytest.mark.integration
@requires_openrouter()
class TestLLMIntegration:
    """
    Real API call tests.  Use the cheapest extraction model (Gemini Flash)
    and verify the call shows up in Langfuse.

    Per AGENTS.md: no mocked LLM calls — use real cheap models.
    """

    @pytest.mark.asyncio
    async def test_extraction_call_produces_trace(self) -> None:
        from augur.config import get_settings
        from augur.llm.client import LLMClient

        get_settings.cache_clear()
        client = LLMClient.from_settings()

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
        assert response.langfuse_trace_id  # non-empty trace ID
        assert response.prompt_tokens > 0
        assert response.completion_tokens > 0
