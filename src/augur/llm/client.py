"""
Augur LLM client.

A thin abstraction over OpenRouter that enforces three invariants:

1. Every call is recorded in `llm_calls` (see augur.llm.call_log) with stage,
   model, prompt template ID, the caller's entity IDs, input, output, tokens,
   cost and latency.  The row is written before the call is made, so there
   are no silent LLM calls: if it cannot be written, the call is not made.

2. Two API keys are managed internally — the main pipeline key and the
   free-tier conversation key.  Calling code specifies a stage; the client
   selects the correct key automatically.

3. A daily budget cap is enforced for the main key.  Once the day's spend
   reaches the configured limit, the client raises LLMBudgetExceededError
   rather than continuing to bill.  Spend is the real per-call cost
   OpenRouter reports (`usage.cost`, requested with `usage.include`), summed
   from `llm_calls`, so every process shares one cap.  A budgeted call whose
   cost OpenRouter does not report raises LLMCostUnreportedError: an
   unmetered call is a budget leak, and leaks must be loud.

A call quota caps how many calls start per UTC day and per minute, counted
from `llm_calls` (failed calls included, as OpenRouter counts them).  It exists
for OpenRouter's free tier, which limits requests per account rather than
spend.  Over the daily quota the client raises LLMQuotaExceededError; over the
per-minute rate it waits.

Each request also carries OpenRouter's `trace` field (trace_id = our call_id),
so if OpenRouter Broadcast is switched on, its traces join back to llm_calls.

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
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import structlog
from openai import AsyncOpenAI, RateLimitError

from augur.config import Settings, get_settings
from augur.llm.call_log import CallLog, LLMCallEnd, LLMCallStart, PostgresCallLog
from augur.llm.models import (
    DEFAULT_MODEL_ROUTING,
    FREE_TIER_STAGES,
    PipelineStage,
    routing_from_overrides,
)

log = structlog.get_logger(__name__)


class LLMBudgetExceededError(Exception):
    """Raised when the daily LLM spend cap has been reached."""


class LLMQuotaExceededError(LLMBudgetExceededError):
    """Raised when the daily LLM call quota has been reached."""


class LLMCallError(Exception):
    """Raised when an LLM call fails after all retries are exhausted."""


class LLMCostUnreportedError(Exception):
    """Raised when OpenRouter returns a budgeted completion without its cost."""


@dataclass
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    # Primary key of the llm_calls row; recorded as provenance on graph writes
    call_id: str


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _utc_midnight() -> datetime:
    return datetime.combine(_utc_today(), datetime.min.time(), tzinfo=UTC)


# How often a call held back by the per-minute rate re-checks for a free slot
_RATE_POLL_SECONDS = 5.0


def _trace_value(value: Any) -> Any:
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


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
        daily_budget_usd: Decimal,
        call_log: CallLog,
        environment: str = "development",
        model_routing: dict[PipelineStage, str] | None = None,
        daily_call_limit: int | None = None,
        calls_per_minute: int | None = None,
    ) -> None:
        self._daily_budget = daily_budget_usd
        self._call_log = call_log
        self._environment = environment
        self._model_routing = model_routing or dict(DEFAULT_MODEL_ROUTING)
        self._daily_call_limit = daily_call_limit
        self._calls_per_minute = calls_per_minute
        # Serialises the quota check and the call's record within this process,
        # so concurrent calls (lenses run in parallel) cannot all pass one check.
        self._quota_lock = asyncio.Lock()

        # Retries are handled in complete(); the SDK's own retries are disabled
        # so one logical call never becomes several silent billed attempts.

        # Main pipeline client (paid key)
        self._main_client = AsyncOpenAI(
            api_key=openrouter_api_key,
            base_url=openrouter_base_url,
            max_retries=0,
        )

        # Conversation-layer client (free-tier key only)
        self._free_client = AsyncOpenAI(
            api_key=openrouter_free_tier_api_key,
            base_url=openrouter_base_url,
            max_retries=0,
        )

    @classmethod
    def from_settings(
        cls, settings: Settings | None = None, *, call_log: CallLog | None = None
    ) -> LLMClient:
        cfg = settings or get_settings()
        return cls(
            openrouter_api_key=cfg.openrouter_api_key,
            openrouter_free_tier_api_key=cfg.openrouter_free_tier_api_key,
            openrouter_base_url=cfg.openrouter_base_url,
            daily_budget_usd=cfg.daily_llm_budget_usd,
            call_log=call_log or PostgresCallLog(),
            environment=cfg.augur_env,
            model_routing=routing_from_overrides(
                {
                    PipelineStage.EXTRACTION: cfg.augur_model_extraction,
                    PipelineStage.ANCHORING: cfg.augur_model_anchoring,
                    PipelineStage.DISCONFIRMATION: cfg.augur_model_disconfirmation,
                    PipelineStage.PROJECTION: cfg.augur_model_projection,
                    PipelineStage.CONVERSATION: cfg.augur_model_conversation,
                    PipelineStage.DIGEST: cfg.augur_model_digest,
                }
            ),
            daily_call_limit=cfg.llm_daily_call_limit,
            calls_per_minute=cfg.llm_calls_per_minute,
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
        Make an LLM completion call, recorded in llm_calls.

        Args:
            stage: The pipeline stage (drives model and key selection).
            prompt_template_id: Identifier for the prompt template.
            messages: OpenAI-style message list.
            metadata: Key/value pairs recorded with the call.  Should include
                      entity IDs relevant to the call (payload_id, signal_id,
                      lens_id, etc.).
            max_retries: Number of attempts on transient errors.
            temperature: Sampling temperature (kept low for structured extraction).

        Returns:
            LLMResponse with content, cost and the call_id of the llm_calls row.

        Raises:
            LLMBudgetExceededError: Daily spend cap reached (main key only).
            LLMQuotaExceededError: Daily call quota reached (all stages).
            LLMCallError: All retries exhausted.
            LLMCostUnreportedError: OpenRouter did not report a budgeted call's cost.
        """
        is_free_tier = stage in FREE_TIER_STAGES
        if not is_free_tier:
            spent = await self._call_log.budgeted_spend_for(_utc_today())
            if spent >= self._daily_budget:
                raise LLMBudgetExceededError(
                    f"Daily LLM budget of ${self._daily_budget} exceeded. "
                    f"Current spend: ${spent}. "
                    "The budget resets at midnight UTC."
                )

        model = self.model_for_stage(stage)
        client = self._free_client if is_free_tier else self._main_client
        call_id = uuid.uuid4()
        metadata = metadata or {}

        async with self._quota_lock:
            await self._wait_for_quota()
            # Recorded before the call: if this write fails, nothing is billed.
            await self._call_log.start(
                LLMCallStart(
                    call_id=call_id,
                    stage=str(stage),
                    prompt_template_id=prompt_template_id,
                    model=model,
                    is_free_tier=is_free_tier,
                    temperature=temperature,
                    metadata=metadata,
                    messages=messages,
                )
            )

        trace = {
            "trace_id": str(call_id),
            "trace_name": f"{stage}.{prompt_template_id}",
            "stage": str(stage),
            "prompt_template_id": prompt_template_id,
            "environment": self._environment,
            **{k: _trace_value(v) for k, v in metadata.items()},
        }

        started = time.monotonic()
        attempts = 0
        last_error: Exception | None = None
        response: Any = None
        for attempt in range(max_retries):
            attempts = attempt + 1
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    extra_body={"usage": {"include": True}, "trace": trace},
                )
                break
            except Exception as exc:
                last_error = exc
                event = "llm.rate_limit" if isinstance(exc, RateLimitError) else "llm.call_error"
                log.warning(
                    event,
                    stage=stage,
                    model=model,
                    attempt=attempts,
                    error=str(exc),
                    call_id=str(call_id),
                )
                if attempts < max_retries:
                    await asyncio.sleep(2**attempt)

        latency_ms = int((time.monotonic() - started) * 1000)

        if response is None:
            await self._call_log.finish(
                call_id,
                LLMCallEnd(
                    status="error",
                    attempts=attempts,
                    latency_ms=latency_ms,
                    error=str(last_error),
                ),
            )
            raise LLMCallError(
                f"LLM call for stage={stage} model={model} failed after "
                f"{max_retries} attempts: {last_error}"
            ) from last_error

        content = response.choices[0].message.content or ""
        # The free router answers with whichever model it picked
        served_model = getattr(response, "model", None) or model
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        raw_cost = (usage.model_extra or {}).get("cost") if usage else None
        cost_usd = Decimal(str(raw_cost)) if raw_cost is not None else None
        cost_missing = cost_usd is None and not is_free_tier

        await self._call_log.finish(
            call_id,
            LLMCallEnd(
                status="error" if cost_missing else "ok",
                attempts=attempts,
                latency_ms=latency_ms,
                output=content,
                error="OpenRouter did not report cost" if cost_missing else None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                model=served_model,
            ),
        )

        if cost_missing:
            log.error("llm.cost_unreported", stage=stage, model=model, call_id=str(call_id))
            raise LLMCostUnreportedError(
                f"OpenRouter returned no usage.cost for stage={stage} model={model}; "
                "refusing to leave the call unmetered."
            )

        log.info(
            "llm.complete",
            stage=stage,
            model=served_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=str(cost_usd),
            latency_ms=latency_ms,
            call_id=str(call_id),
        )

        return LLMResponse(
            content=content,
            model=served_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd or Decimal(0),
            call_id=str(call_id),
        )

    async def _wait_for_quota(self) -> None:
        """Raise past the daily quota; wait while the per-minute rate is used up."""
        if self._daily_call_limit is not None:
            used = await self._call_log.calls_since(_utc_midnight())
            if used >= self._daily_call_limit:
                raise LLMQuotaExceededError(
                    f"Daily LLM call quota of {self._daily_call_limit} reached "
                    f"({used} calls today). The quota resets at midnight UTC."
                )
        if self._calls_per_minute:
            while True:
                recent = await self._call_log.calls_since(
                    datetime.now(UTC) - timedelta(minutes=1)
                )
                if recent < self._calls_per_minute:
                    return
                await asyncio.sleep(_RATE_POLL_SECONDS)

    async def health_check(self) -> dict[str, Any]:
        """
        Report key configuration and today's spend.  Makes no LLM call.

        Used by the /health endpoint and the operator CLI.
        """
        call_log_ok = False
        today_spend: Decimal | None = None
        today_calls: int | None = None
        try:
            today_spend = await self._call_log.budgeted_spend_for(_utc_today())
            today_calls = await self._call_log.calls_since(_utc_midnight())
            call_log_ok = True
        except Exception as exc:
            log.warning("llm.health.call_log_unavailable", error=str(exc))

        return {
            "call_log_available": call_log_ok,
            "main_key_configured": bool(self._main_client.api_key),
            "free_tier_key_configured": bool(self._free_client.api_key),
            "daily_budget_usd": str(self._daily_budget),
            "today_spend_usd": str(today_spend) if today_spend is not None else None,
            "daily_call_limit": self._daily_call_limit,
            "today_calls": today_calls,
        }
