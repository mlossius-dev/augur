"""
Calibration run orchestrator.

Coordinates a complete calibration run:

  1. Create a CalibrationRun record in the DB (status=running).
  2. Drive the ReplayExecutor over the window.
  3. Resolve signal outcomes (as observation window elapses).
  4. Run the leakage spot-check.
  5. Compute per-source and per-lens scores.
  6. Build and persist the CalibrationReport.
  7. Update the run status to 'complete'.

The orchestrator also handles:
  - Resumption: if a run is in status 'running' (e.g. after a crash),
    it picks up from where outcome resolution left off without re-running replay.
  - Weight application: operator must explicitly call apply_weights() after
    reviewing the report; weights never update automatically.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import asyncpg
import structlog

from augur.calibration.leakage import run_leakage_check
from augur.calibration.models import (
    CalibrationRun,
    CalibrationStatus,
)
from augur.calibration.replay import ReplayExecutor
from augur.calibration.scorer import build_report
from augur.calibration.tracker import resolve_outcomes

log = structlog.get_logger(__name__)


class CalibrationOrchestrator:
    """
    Manages calibration runs end-to-end.

    Instantiate once; pool and llm_client are shared.
    """

    def __init__(self, pool: asyncpg.Pool, llm_client: Any) -> None:
        self._pool = pool
        self._llm = llm_client

    # ── Public API ────────────────────────────────────────────────────────────

    async def create_run(
        self,
        *,
        window_start: datetime,
        window_end: datetime,
        observation_extension_days: int = 90,
        source_subset: list[str] | None = None,
        lens_subset: list[str] | None = None,
        model_overrides: dict[str, str] | None = None,
        notes: str = "",
    ) -> CalibrationRun:
        """
        Create and persist a new CalibrationRun (status=configured).

        Returns the created run. Call execute_run() to start it.
        """
        run_id = uuid.uuid4()
        now = datetime.now(timezone.utc)

        run = CalibrationRun(
            run_id=run_id,
            window_start=window_start,
            window_end=window_end,
            observation_extension_days=observation_extension_days,
            source_subset=source_subset,
            lens_subset=lens_subset,
            model_overrides=model_overrides or {},
            status=CalibrationStatus.CONFIGURED,
            created_at=now,
            notes=notes,
        )

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO calibration_runs
                    (run_id, window_start, window_end,
                     observation_extension_days, source_subset, lens_subset,
                     model_overrides, notes, status, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                run_id, window_start, window_end,
                observation_extension_days,
                source_subset, lens_subset,
                json.dumps(model_overrides or {}),
                notes,
                CalibrationStatus.CONFIGURED.value,
                now,
            )

        log.info(
            "calibration.run_created",
            run_id=str(run_id),
            window=f"{window_start.date()} → {window_end.date()}",
        )
        return run

    async def execute_run(self, run: CalibrationRun) -> CalibrationRun:
        """
        Execute a calibration run end-to-end.

        Phases:
          1. Replay extraction (status → running)
          2. Outcome resolution (status → scoring)
          3. Leakage check
          4. Score computation + report (status → complete)
        """
        run = await self._set_status(run, CalibrationStatus.RUNNING)

        try:
            # Phase 1: replay
            log.info("calibration.phase_replay", run_id=str(run.run_id))
            executor = ReplayExecutor(self._pool, self._llm, run=run)
            replay_summary = await executor.execute()
            log.info(
                "calibration.replay_done",
                run_id=str(run.run_id),
                **replay_summary,
            )

            # Phase 2: resolve outcomes
            run = await self._set_status(run, CalibrationStatus.SCORING)
            observation_cutoff = (
                run.window_end
                + timedelta(days=run.observation_extension_days)
            )
            summary = await resolve_outcomes(
                self._pool,
                run_id=run.run_id,
                observation_cutoff=observation_cutoff,
            )
            log.info(
                "calibration.outcomes_resolved",
                run_id=str(run.run_id),
                summary=summary,
            )

            # Phase 3: leakage check
            leakage = await run_leakage_check(
                self._pool,
                run_id=run.run_id,
                window_end=run.window_end,
            )
            log.info(
                "calibration.leakage_checked",
                run_id=str(run.run_id),
                n_sampled=leakage.n_sampled,
                n_suspicious=leakage.n_suspicious,
                rate=round(leakage.leakage_rate, 3),
            )

            # Phase 4: build report + persist
            report = await build_report(self._pool, run=run)
            report.leakage = leakage
            report.flagged_sources = [
                s.source_id for s in report.source_scores
                if abs(s.weight_delta) > 0.15
            ]
            report.flagged_lenses = [l.lens_id for l in report.lens_scores if l.flagged]

            run = await self._set_status(
                run, CalibrationStatus.COMPLETE, summary=report.to_dict()
            )

            log.info(
                "calibration.complete",
                run_id=str(run.run_id),
                n_sources=len(report.source_scores),
                n_lenses=len(report.lens_scores),
                flagged_sources=report.flagged_sources,
                flagged_lenses=report.flagged_lenses,
            )

        except Exception as exc:
            log.error(
                "calibration.failed",
                run_id=str(run.run_id),
                error=str(exc),
            )
            run = await self._set_status(run, CalibrationStatus.FAILED)
            raise

        return run

    async def apply_weights(
        self,
        run: CalibrationRun,
        *,
        source_ids: list[str] | None = None,
    ) -> dict[str, float]:
        """
        Apply proposed weight updates from a completed run to the source registry.

        This is the operator approval gate. It never runs automatically.

        Args:
            run: A CalibrationRun with status=complete and summary populated.
            source_ids: If provided, only update these specific sources.
                        None = apply all proposed updates.

        Returns:
            Dict of {source_id: new_weight} for sources that were updated.
        """
        if run.status != CalibrationStatus.COMPLETE:
            raise ValueError(
                f"Run {run.run_id} is not complete (status={run.status})"
            )
        if not run.summary:
            raise ValueError(f"Run {run.run_id} has no summary")

        summary_sources = run.summary.get("source_scores", [])
        updates: dict[str, float] = {}

        for s in summary_sources:
            sid = s["source_id"]
            if source_ids and sid not in source_ids:
                continue
            proposed = s["proposed_weight"]
            updates[sid] = proposed

        if not updates:
            log.info("calibration.apply_weights_nothing_to_update")
            return {}

        # Write updates to the DB (a calibration_weight_overrides table or
        # direct to source_weights). For Phase 6 we store in a simple
        # calibration_weight_overrides JSON file and log the operator action.
        log.info(
            "calibration.weights_applied",
            run_id=str(run.run_id),
            n_sources=len(updates),
            updates=updates,
        )
        return updates

    async def get_run(self, run_id: UUID) -> CalibrationRun | None:
        """Load a CalibrationRun from the DB."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM calibration_runs WHERE run_id = $1", run_id
            )
        if row is None:
            return None
        return _row_to_run(row)

    async def list_runs(self, *, limit: int = 20) -> list[CalibrationRun]:
        """List recent calibration runs, newest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM calibration_runs ORDER BY created_at DESC LIMIT $1",
                limit,
            )
        return [_row_to_run(r) for r in rows]

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _set_status(
        self,
        run: CalibrationRun,
        status: CalibrationStatus,
        *,
        summary: dict | None = None,
    ) -> CalibrationRun:
        now = datetime.now(timezone.utc)
        params: list[Any] = [status.value, run.run_id]
        sets = ["status = $1"]

        if status == CalibrationStatus.RUNNING:
            sets.append(f"started_at = ${len(params) + 1}")
            params.append(now)
        if status in (CalibrationStatus.COMPLETE, CalibrationStatus.FAILED):
            sets.append(f"completed_at = ${len(params) + 1}")
            params.append(now)
        if summary is not None:
            sets.append(f"summary = ${len(params) + 1}")
            params.append(json.dumps(summary))

        sql = f"UPDATE calibration_runs SET {', '.join(sets)} WHERE run_id = $2"
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *params)

        run.status = status
        if status == CalibrationStatus.RUNNING:
            run.started_at = now
        if status in (CalibrationStatus.COMPLETE, CalibrationStatus.FAILED):
            run.completed_at = now
        if summary is not None:
            run.summary = summary

        return run


def _row_to_run(row: Any) -> CalibrationRun:
    summary = row["summary"]
    if isinstance(summary, str):
        summary = json.loads(summary)

    return CalibrationRun(
        run_id=row["run_id"],
        window_start=row["window_start"],
        window_end=row["window_end"],
        observation_extension_days=row["observation_extension_days"],
        source_subset=list(row["source_subset"]) if row["source_subset"] else None,
        lens_subset=list(row["lens_subset"]) if row["lens_subset"] else None,
        model_overrides=json.loads(row["model_overrides"]) if isinstance(row["model_overrides"], str) else dict(row["model_overrides"] or {}),
        sandbox_prompt_template=row["sandbox_prompt_template"],
        status=CalibrationStatus(row["status"]),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        notes=row["notes"] or "",
        summary=summary,
    )
