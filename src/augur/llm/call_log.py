"""
The LLM call log: Augur's own record of every LLM call.

Every call is written to `llm_calls` (migration 011) before it is made and
completed afterwards, so the log is the audit trail ("which call produced
this edge?") and the analysis surface (cost, latency and failures by stage,
model and prompt template) in one table next to the graph it explains.

The same table feeds the daily budget and the call quota: spend is the sum of
today's reported costs for budgeted (non-free-tier) calls, and the quota counts
every attempt of every call started, so both hold across the API server, the scheduler and the
CLI, and survive restarts.

The in-memory log exists for tests and single-process tools.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

import asyncpg


@dataclass
class LLMCallStart:
    call_id: UUID
    stage: str
    prompt_template_id: str
    model: str
    is_free_tier: bool
    temperature: float
    metadata: dict[str, Any]
    messages: list[dict[str, str]]


@dataclass
class LLMCallEnd:
    status: str  # ok | error
    attempts: int
    latency_ms: int
    output: str | None = None
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: Decimal | None = None
    # The model that answered, when it differs from the one requested (the
    # free router picks one per call)
    model: str | None = None


class CallLog(Protocol):
    async def start(self, call: LLMCallStart) -> None: ...

    async def finish(self, call_id: UUID, end: LLMCallEnd) -> None: ...

    async def budgeted_spend_for(self, day: date) -> Decimal: ...

    async def calls_since(self, since: datetime) -> int: ...


def _json(value: Any) -> str:
    # Metadata carries UUIDs, datetimes and enums; store their string form.
    return json.dumps(value, default=str)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


class PostgresCallLog:
    """
    Call log backed by `llm_calls`.

    Takes the pool lazily so the client can be built before the database is
    initialised (as the CLI does); the pool must exist by the first call.
    """

    def __init__(self, pool: asyncpg.Pool | None = None) -> None:
        self._pool = pool

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            from augur.db.connection import get_raw_pool

            self._pool = get_raw_pool()
        return self._pool

    async def start(self, call: LLMCallStart) -> None:
        await self._get_pool().execute(
            """
            INSERT INTO llm_calls
                (call_id, stage, prompt_template_id, model, is_free_tier,
                 temperature, metadata, messages)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb)
            """,
            call.call_id,
            call.stage,
            call.prompt_template_id,
            call.model,
            call.is_free_tier,
            call.temperature,
            _json(call.metadata),
            _json(call.messages),
        )

    async def finish(self, call_id: UUID, end: LLMCallEnd) -> None:
        await self._get_pool().execute(
            """
            UPDATE llm_calls
               SET finished_at = now(), status = $2, attempts = $3,
                   latency_ms = $4, output = $5, error = $6,
                   prompt_tokens = $7, completion_tokens = $8, cost_usd = $9,
                   model = COALESCE($10, model)
             WHERE call_id = $1
            """,
            call_id,
            end.status,
            end.attempts,
            end.latency_ms,
            end.output,
            end.error,
            end.prompt_tokens,
            end.completion_tokens,
            end.cost_usd,
            end.model,
        )

    async def calls_since(self, since: datetime) -> int:
        return await self._get_pool().fetchval(
            # OpenRouter counts every attempt; a call still in flight counts once
            "SELECT COALESCE(sum(GREATEST(attempts, 1)), 0) FROM llm_calls"
            " WHERE called_at >= $1",
            since,
        )

    async def budgeted_spend_for(self, day: date) -> Decimal:
        start, end = _day_bounds(day)
        value = await self._get_pool().fetchval(
            """
            SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls
             WHERE NOT is_free_tier AND called_at >= $1 AND called_at < $2
            """,
            start,
            end,
        )
        return Decimal(value)


@dataclass
class InMemoryCallLog:
    calls: dict[UUID, tuple[LLMCallStart, LLMCallEnd | None, datetime]] = field(
        default_factory=dict
    )

    async def start(self, call: LLMCallStart) -> None:
        self.calls[call.call_id] = (call, None, datetime.now(UTC))

    async def finish(self, call_id: UUID, end: LLMCallEnd) -> None:
        start, _, called_at = self.calls[call_id]
        self.calls[call_id] = (start, end, called_at)

    async def calls_since(self, since: datetime) -> int:
        return sum(
            max(end.attempts if end else 0, 1)
            for _, end, called_at in self.calls.values()
            if called_at >= since
        )

    async def budgeted_spend_for(self, day: date) -> Decimal:
        total = Decimal(0)
        for start, end, called_at in self.calls.values():
            if start.is_free_tier or end is None or end.cost_usd is None:
                continue
            if called_at.date() == day:
                total += end.cost_usd
        return total
