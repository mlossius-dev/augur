"""
The 2026 historical backfill (docs/augur-architecture.md, "Historical backfill").

Newest day first: from the day before live ingestion began, backwards to the
start date, one day at a time.  For each day:

  1. read the admitted RSS feeds' archived snapshots (Wayback) and keep the
     items published that day;
  2. draw at most `per_day`, round-robin with a per-source cap;
  3. store them as payloads, exactly as live ingestion does (marked backfill);
  4. extract with all active lenses on the backfill model, sandboxed to the
     day, signals marked origin='backfill';
  5. anchor the day's backfill signals on the same model.

The Applier keeps the graph consistent whatever the order
(docs/augur-graph-schema.md, "Time order versus processing order").  The run
stops when the backfill's own spend cap or the daily budget is reached.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

import asyncpg
import httpx
import structlog

from augur.anchoring.orchestrator import AnchoringOrchestrator
from augur.backfill.selection import draw_day
from augur.backfill.wayback import WaybackFeedFetcher
from augur.config import Settings
from augur.extraction.executor import LensExecutor
from augur.extraction.lenses import ACTIVE_LENSES
from augur.extraction.tier_a import TierAStore
from augur.ingestion.pipeline import IngestionPipeline
from augur.ingestion.source_registry import get_enabled_sources
from augur.llm.call_log import PostgresCallLog
from augur.llm.client import LLMBudgetExceededError, LLMClient
from augur.llm.models import PipelineStage

log = structlog.get_logger(__name__)

BACKFILL_START = date(2026, 1, 1)  # operator decision: 2026 only


@dataclass
class DayResult:
    day: date
    status: str = "done"
    n_found: int = 0
    n_payloads: int = 0
    n_signals: int = 0
    n_applied: int = 0
    cost_usd: Decimal = Decimal(0)
    by_source: dict[str, int] = field(default_factory=dict)
    stop_reason: str | None = None


def backfill_llm_client(settings: Settings) -> LLMClient:
    """Extraction and anchoring on the paid backfill model; no free-tier quota applies."""
    model = (settings.backfill_model,)
    routing = {stage: model for stage in PipelineStage}
    return LLMClient(
        openrouter_api_key=settings.openrouter_api_key,
        openrouter_free_tier_api_key=settings.openrouter_free_tier_api_key,
        openrouter_base_url=settings.openrouter_base_url,
        daily_budget_usd=settings.daily_llm_budget_usd,
        call_log=PostgresCallLog(),
        environment=settings.augur_env,
        model_routing=routing,
        extra_request={"reasoning": {"enabled": False}} if settings.backfill_reasoning_off else None,
    )


class BackfillRunner:
    def __init__(self, pool: asyncpg.Pool, settings: Settings) -> None:
        self._pool = pool
        self._settings = settings
        self._llm = backfill_llm_client(settings)
        self._pipeline = IngestionPipeline(pool, archive_root=settings.payload_archive_root)
        self._executor = LensExecutor(self._llm, origin="backfill")
        self._tier_a = TierAStore(pool)
        self._anchoring = AnchoringOrchestrator(pool, self._llm, call_metadata={"backfill": True})

    # ── State ──────────────────────────────────────────────────────────────

    async def spent(self) -> Decimal:
        """Total cost of the backfill's calls so far."""
        value = await self._pool.fetchval(
            "SELECT COALESCE(sum(cost_usd), 0) FROM llm_calls WHERE metadata->>'backfill' = 'true'"
        )
        return Decimal(str(value))

    async def next_day(self) -> date | None:
        """
        A day left partial (budget or interruption) first; else the day before
        the oldest one done, or before live ingestion began.  None when finished.
        """
        partial = await self._pool.fetchval(
            "SELECT max(day) FROM backfill_days WHERE status = 'partial'"
        )
        if partial is not None:
            return partial
        oldest_done = await self._pool.fetchval("SELECT min(day) FROM backfill_days")
        if oldest_done is None:
            live_start = await self._pool.fetchval(
                "SELECT min(fetched_at)::date FROM payloads WHERE NOT (metadata ? 'backfill')"
            )
            if live_start is None:
                return None
            candidate = live_start - timedelta(days=1)
        else:
            candidate = oldest_done - timedelta(days=1)
        return candidate if candidate >= BACKFILL_START else None

    # ── One day ────────────────────────────────────────────────────────────

    async def draw(self, day: date) -> tuple[list[Any], dict[str, int]]:
        """Read the day from the archive and draw its payloads (no LLM, no writes)."""
        sources = [s for s in get_enabled_sources() if s.access_method == "rss"]
        by_source: dict[str, list[Any]] = {}
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            fetcher = WaybackFeedFetcher(client)
            for source in sources:
                items = await fetcher.fetch_day(source, day)
                if items:
                    by_source[source.source_id] = items
        found = {k: len(v) for k, v in by_source.items()}
        drawn = draw_day(
            by_source,
            per_day=self._settings.backfill_per_day,
            per_source_cap=self._settings.backfill_per_source_cap,
        )
        return drawn, found

    async def run_day(self, day: date) -> DayResult:
        result = DayResult(day=day)
        spent_before = await self.spent()
        drawn, found = await self.draw(day)
        result.n_found = sum(found.values())

        # Store as payloads, per source, exactly as live ingestion does
        stored: list[dict[str, Any]] = []
        for source_id in sorted({fr.source_id for fr in drawn}):
            items = [fr for fr in drawn if fr.source_id == source_id]
            kept = await self._pipeline.store_fetch_results(source_id, items)
            result.by_source[source_id] = len(kept)
            stored.extend(kept)
        result.n_payloads = len(stored)

        # Extract, within the spend cap: every backfilled payload of the day not
        # yet extracted, including those stored by an earlier, stopped run
        pending = await self._pool.fetch(
            """
            SELECT p.payload_id, p.content, p.content_timestamp, p.source_id
            FROM payloads p
            WHERE p.metadata ? 'backfill' AND NOT p.rejected
              AND p.content_timestamp >= $1 AND p.content_timestamp < $1 + interval '1 day'
              AND NOT EXISTS (
                  SELECT 1 FROM llm_calls c
                  WHERE c.stage = 'extraction' AND c.status = 'ok'
                    AND c.metadata->>'payload_id' = p.payload_id::text)
            ORDER BY p.content_timestamp DESC
            """,
            datetime.combine(day, time(0), tzinfo=timezone.utc),
        )
        for payload in pending:
            if await self.spent() >= Decimal(str(self._settings.backfill_budget_usd)):
                result.status, result.stop_reason = "partial", "backfill budget reached"
                break
            try:
                signals = await self._executor.extract_all_lenses(
                    payload_id=payload["payload_id"],
                    content=payload["content"],
                    content_timestamp=payload["content_timestamp"],
                    source_id=payload["source_id"],
                    lenses=ACTIVE_LENSES,
                )
            except LLMBudgetExceededError as exc:
                result.status, result.stop_reason = "partial", str(exc)
                break
            if signals:
                signals = await self._tier_a.deduplicate_batch(signals)
                result.n_signals += await self._tier_a.store_signals(signals)

        # Anchor the backfill signals (this day's, plus any left from a stopped day)
        cycle = await self._anchoring.run_cycle(
            min_age_hours=0, limit=500, force=True, max_hold_hours=None, origin="backfill"
        )
        result.n_applied = cycle.n_applied
        result.cost_usd = await self.spent() - spent_before
        await self._record(result)
        log.info("backfill.day_done", day=str(day), status=result.status,
                 found=result.n_found, payloads=result.n_payloads, signals=result.n_signals,
                 applied=result.n_applied, cost_usd=str(result.cost_usd))
        return result

    async def _record(self, r: DayResult) -> None:
        await self._pool.execute(
            """
            INSERT INTO backfill_days
                (day, status, n_found, n_payloads, n_signals, n_applied, cost_usd, by_source)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (day) DO UPDATE SET
                status = EXCLUDED.status, n_found = EXCLUDED.n_found,
                n_payloads = backfill_days.n_payloads + EXCLUDED.n_payloads,
                n_signals = backfill_days.n_signals + EXCLUDED.n_signals,
                n_applied = backfill_days.n_applied + EXCLUDED.n_applied,
                cost_usd = backfill_days.cost_usd + EXCLUDED.cost_usd,
                by_source = EXCLUDED.by_source, finished_at = now()
            """,
            r.day, r.status, r.n_found, r.n_payloads, r.n_signals, r.n_applied,
            r.cost_usd, json.dumps(r.by_source),
        )

    # ── Many days ──────────────────────────────────────────────────────────

    async def run(self, *, max_days: int) -> list[DayResult]:
        results: list[DayResult] = []
        for _ in range(max_days):
            day = await self.next_day()
            if day is None:
                log.info("backfill.finished")
                break
            result = await self.run_day(day)
            results.append(result)
            if result.status != "done":
                log.info("backfill.stopped", day=str(day), reason=result.stop_reason)
                break
        return results
