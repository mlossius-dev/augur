"""
Replay-mode execution engine for calibration runs.

Drives the replay pipeline:
  1. Walk payloads in the calibration window in chronological order.
  2. For each payload, run all lenses with the sandbox prompt injected.
  3. Store signals with their content_timestamp (not now()).
  4. Register each extracted signal in signal_outcome_tracking as 'pending'.
  5. Normal anchoring and disconfirmation run against the replay graph state.

The sandbox injection modifies every extraction LLM call to include an
explicit instruction that the model should treat `replay_date` as the current
date and must not reason about events after that date.

Look-ahead bias countermeasures:
  - Sandbox prompt in every extraction call.
  - Model selection filtered by training cutoff when model_overrides are set.
  - Operator spot-check tooling in leakage.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from augur.calibration.models import CalibrationRun, CalibrationStatus

log = structlog.get_logger(__name__)

# ── Sandbox prompt template ───────────────────────────────────────────────────

SANDBOX_PROMPT_TEMPLATE = """\
IMPORTANT — REPLAY MODE INSTRUCTION:

You are reading this content on {replay_date}. The current date for your
purposes is {replay_date}. Do not reason about or reference events that
occurred after this date, even if you have knowledge of them from your
training data.

Produce signals based only on what could be known to a reader on {replay_date},
using only the information present in the payload below.

This instruction takes precedence over any implicit assumptions about the
current date.

---
"""


def build_sandbox_system_prompt(base_prompt: str, replay_date: datetime) -> str:
    """
    Prepend the sandbox instruction to a lens system prompt.

    Called by the replay executor for every LLM completion.
    """
    date_str = replay_date.strftime("%Y-%m-%d")
    sandbox = SANDBOX_PROMPT_TEMPLATE.format(replay_date=date_str)
    return sandbox + base_prompt


class ReplayExecutor:
    """
    Runs the extraction pipeline in replay mode over a calibration window.

    Processes payloads in chronological order, injecting sandbox prompts
    and registering each signal in signal_outcome_tracking.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        llm_client: Any,
        *,
        run: CalibrationRun,
    ) -> None:
        self._pool = pool
        self._llm = llm_client
        self._run = run

    async def execute(self) -> dict[str, int]:
        """
        Drive the replay over the configured window.

        Returns summary: {n_payloads, n_signals, n_registered}.
        """
        from augur.calibration.tracker import register_signals_for_run
        from augur.extraction.executor import LensExecutor
        from augur.extraction.lenses import ACTIVE_LENSES
        from augur.extraction.tier_a import TierAStore

        # Apply lens subset filter
        lenses = ACTIVE_LENSES
        if self._run.lens_subset:
            lenses = [l for l in lenses if l.lens_id in self._run.lens_subset]

        executor = LensExecutor(self._llm)
        tier_a = TierAStore(self._pool)

        # Load payloads in the window in chronological order
        payload_rows = await self._load_payloads()
        log.info(
            "replay.start",
            run_id=str(self._run.run_id),
            n_payloads=len(payload_rows),
            window_start=self._run.window_start.isoformat(),
            window_end=self._run.window_end.isoformat(),
        )

        n_signals = 0
        n_registered = 0

        for row in payload_rows:
            content_ts: datetime = row["content_timestamp"]
            payload_id: UUID = row["payload_id"]
            source_id: str = row["source_id"]
            content: str = row["content"]

            # Build sandboxed lenses for this replay timestamp
            sandboxed_lenses = [
                _sandbox_lens(l, content_ts) for l in lenses
                if self._run.source_subset is None
                or source_id in self._run.source_subset
            ]
            if not sandboxed_lenses:
                continue

            signals = await executor.extract_all_lenses(
                payload_id=payload_id,
                content=content,
                content_timestamp=content_ts,
                source_id=source_id,
                lenses=sandboxed_lenses,
            )

            if signals:
                signals = await tier_a.deduplicate_batch(signals)
                stored = await tier_a.store_signals(signals)
                n_signals += stored

                # Register stored signals in outcome tracking
                reg = await register_signals_for_run(
                    self._pool,
                    run_id=self._run.run_id,
                    signals=signals,
                )
                n_registered += reg

        log.info(
            "replay.complete",
            run_id=str(self._run.run_id),
            n_payloads=len(payload_rows),
            n_signals=n_signals,
            n_registered=n_registered,
        )
        return {
            "n_payloads": len(payload_rows),
            "n_signals": n_signals,
            "n_registered": n_registered,
        }

    async def _load_payloads(self) -> list[Any]:
        """Load payloads in the calibration window, ordered by content_timestamp."""
        params: list[Any] = [
            self._run.window_start,
            self._run.window_end,
        ]
        conditions = [
            "content_timestamp >= $1",
            "content_timestamp <= $2",
            "NOT rejected",
        ]

        if self._run.source_subset:
            conditions.append(f"source_id = ANY(${len(params) + 1}::text[])")
            params.append(self._run.source_subset)

        where = " AND ".join(conditions)
        sql = (
            f"SELECT payload_id, source_id, content, content_timestamp "
            f"FROM payloads WHERE {where} "
            f"ORDER BY content_timestamp ASC"
        )

        async with self._pool.acquire() as conn:
            return await conn.fetch(sql, *params)


def _sandbox_lens(lens: Any, replay_date: datetime) -> Any:
    """
    Return a copy of `lens` with the sandbox instruction prepended to
    its system_prompt.  Uses dataclass replace so the original is unchanged.
    """
    import dataclasses

    sandboxed_prompt = build_sandbox_system_prompt(
        lens.system_prompt, replay_date
    )
    return dataclasses.replace(lens, system_prompt=sandboxed_prompt)
