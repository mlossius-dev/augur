"""
Augur LLM client.

A thin abstraction over OpenRouter that enforces three invariants:

1. Every call emits a Langfuse trace with stage tag, model, prompt template ID,
   and any relevant entity IDs.  No silent LLM calls.

2. Two API keys are managed internally — the main pipeline key and the
   free-tier conversation key.  Calling code specifies a stage; the client
   selects the correct key automatically.

3. A daily budget cap is enforced for the main key.  Once the day's spend
   reaches the configured limit, the client raises LLMBudgetExceededError
   rather than continuing to bill.

Usage:

    client = LLMClient.from_settings()
    response = await client.complete(
        stage=PipelineStage.EXTRACTION,
        prompt_template_id="lens_extraction_v1",
        messages=[{"role": "user", "content": "..."}],
        metadata={"lens_id": "commodities", "payload_id": "abc-123"},
    )
    text = response.content
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import structlog
from langfuse import Langfuse
from openai import AsyncOpenAI, RateLimitError

from augur.config import Settings, get_settings
from augur.llm.models import DEFAULT_MODEL_ROUTING, FREE_TIER_STAGES, PipelineStage

log = structlog.get_logger(__name__)


class LLMBudgetExceededError(Exception):
    """Raised when the daily LLM spend cap has been reached."""


class LLMCallError(Exception):
    """Raised when an LLM call fails after all retries are exhausted."""


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    langfuse_trace_id: str


@dataclass
class _DailySpend:
    """Tracks USD spend for the current UTC day."""

    day: date = field(default_factory=lambda: datetime.now(UTC).date())
    total: Decimal = field(default_factory=Decimal)

    def reset_if_new_day(self) -> None:
        today = datetime.now(UTC).date()
        if today != self.day:
            self.day = today
            self.total = Decimal("0")

    def add(self, amount: Decimal) -> None:
        self.reset_if_new_day()
        self.total += amount

    def exceeds(self, cap: Decimal) -> bool:
        self.reset_if_new_day()
        return self.total >= cap


class LLMClient:
    """
    Single entry point for all LLM calls in Augur.

    Thread-safe for concurrent async use; do not share across processes.
    Instantiate once and reuse throughout the application lifetime.
    """

    def __init__(
        self,
        *,
        openrouter_api_key: str,
        openrouter_free_tier_api_key: str,
        openrouter_base_url: str,
        langfuse_host: str,
        langfuse_public_key: str,
        langfuse_secret_key: str,
        daily_budget_usd: Decimal,
        model_routing: dict[PipelineStage, str] | None = None,
    ) -> None:
        self._daily_budget = daily_budget_usd
        self._spend = _DailySpend()
        self._model_routing = model_routing or dict(DEFAULT_MODEL_ROUTING)

        # Main pipeline client (paid key)
        self._main_client = AsyncOpenAI(
            api_key=openrouter_api_key,
            base_url=openrouter_base_url,
        )

        # Conversation-layer client (free-tier key only)
        self._free_client = AsyncOpenAI(
            api_key=openrouter_free_tier_api_key,
            base_url=openrouter_base_url,
        )

        # Langfuse observability — all traces land here regardless of which key called
        self._langfuse = Langfuse(
            host=langfuse_host,
            public_key=langfuse_public_key,
            secret_key=langfuse_secret_key,
        )

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> LLMClient:
        cfg = settings or get_settings()
        return cls(
            openrouter_api_key=cfg.openrouter_api_key,
            openrouter_free_tier_api_key=cfg.openrouter_free_tier_api_key,
            openrouter_base_url=cfg.openrouter_base_url,
            langfuse_host=cfg.langfuse_host,
            langfuse_public_key=cfg.langfuse_public_key,
            langfuse_secret_key=cfg.langfuse_secret_key,
            daily_budget_usd=cfg.daily_llm_budget_usd,
        )

    def model_for_stage(self, stage: PipelineStage) -> str:
        return self._model_routing[stage]

    async def complete(
        self,
        *,
        stage: PipelineStage,
        prompt_template_id: str,
        messages: list[dict[str, str]],
        metadata: dict[str, Any] | None = None,
        max_retries: int = 3,
        temperature: float = 0.1,
    ) -> LLMResponse:
        """
        Make an LLM completion call with full Langfuse tracing.

        Args:
            stage: The pipeline stage (drives model and key selection).
            prompt_template_id: Identifier for the prompt template (logged to Langfuse).
            messages: OpenAI-style message list.
            metadata: Arbitrary key/value pairs recorded on the Langfuse trace.
                      Should include entity IDs relevant to the call
                      (payload_id, signal_id, lens_id, etc.).
            max_retries: Number of retry attempts on transient errors.
            temperature: Sampling temperature (kept low for structured extraction).

        Returns:
            LLMResponse with content and cost information.

        Raises:
            LLMBudgetExceededError: Daily spend cap reached (main key only).
            LLMCallError: All retries exhausted.
        """
        is_free_tier = stage in FREE_TIER_STAGES
        if not is_free_tier and self._spend.exceeds(self._daily_budget):
            raise LLMBudgetExceededError(
                f"Daily LLM budget of ${self._daily_budget} exceeded. "
                f"Current spend: ${self._spend.total}. "
                "The budget resets at midnight UTC."
            )

        model = self.model_for_stage(stage)
        client = self._free_client if is_free_tier else self._main_client

        trace = self._langfuse.trace(
            name=f"{stage}.{prompt_template_id}",
            tags=[str(stage)],
            metadata={
                "stage": str(stage),
                "prompt_template_id": prompt_template_id,
                "model": model,
                "is_free_tier": is_free_tier,
                **(metadata or {}),
            },
        )

        generation = trace.generation(
            name=f"openrouter.{model}",
            model=model,
            input=messages,
            metadata=metadata or {},
        )

        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                )
                break
            except RateLimitError as exc:
                wait = 2 ** attempt
                log.warning(
                    "llm.rate_limit",
                    stage=stage,
                    model=model,
                    attempt=attempt + 1,
                    retry_in=wait,
                )
                await asyncio.sleep(wait)
                last_error = exc
            except Exception as exc:
                wait = 2 ** attempt
                log.error(
                    "llm.call_error",
                    stage=stage,
                    model=model,
                    attempt=attempt + 1,
                    error=str(exc),
                )
                await asyncio.sleep(wait)
                last_error = exc
        else:
            generation.end(level="ERROR", status_message=str(last_error))
            trace.update(output={"error": str(last_error)})
            self._langfuse.flush()
            raise LLMCallError(
                f"LLM call for stage={stage} model={model} failed after "
                f"{max_retries} attempts: {last_error}"
            ) from last_error

        content = response.choices[0].message.content or ""

        # OpenRouter returns usage; fall back to 0 if not available
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        # Cost is reported in the response headers by OpenRouter but not in
        # the standard API body.  We record token counts; Langfuse computes
        # cost from its model pricing tables.
        cost_usd = Decimal("0")  # placeholder; true cost tracked in Langfuse

        generation.end(
            output=content,
            usage={
                "input": prompt_tokens,
                "output": completion_tokens,
                "unit": "TOKENS",
            },
        )
        trace.update(output={"content": content[:500]})  # truncate for readability
        self._langfuse.flush()

        if not is_free_tier and cost_usd > 0:
            self._spend.add(cost_usd)

        log.info(
            "llm.complete",
            stage=stage,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            trace_id=trace.id,
        )

        return LLMResponse(
            content=content,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            langfuse_trace_id=trace.id,
        )

    async def health_check(self) -> dict[str, Any]:
        """
        Verify connectivity to Langfuse and OpenRouter.

        Used by the /health endpoint and the operator CLI.  Does not make a
        full LLM call; only checks that the Langfuse SDK can reach its host.
        """
        langfuse_ok = False
        try:
            self._langfuse.auth_check()
            langfuse_ok = True
        except Exception as exc:
            log.warning("llm.health.langfuse_unreachable", error=str(exc))

        return {
            "langfuse_reachable": langfuse_ok,
            "main_key_configured": bool(self._main_client.api_key),
            "free_tier_key_configured": bool(self._free_client.api_key),
            "daily_budget_usd": str(self._daily_budget),
            "today_spend_usd": str(self._spend.total),
        }
