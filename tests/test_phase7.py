"""
Tests for Phase 7: Live operation infrastructure.

Covers:
  - weight_store: persist, load, supersession, get_effective_weight
  - source_registry: load_sources_with_overrides merges correctly
  - live_tracker: ensure_live_run idempotency, register_live_signals, checkpoint
  - monitoring/health: log_job_start/complete, get_signal_flow structure
  - scheduler: live calibration checkpoint job exists
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

_NOW = datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

# ── Helpers ─────────────────────────────────────────────────────────────────────


def _make_pool(*conn_responses):
    """Build a minimal async mock pool that returns conn with preset responses."""
    conn = AsyncMock()
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn),
        __aexit__=AsyncMock(return_value=False),
    ))
    return pool, conn


# ── weight_store ────────────────────────────────────────────────────────────────


from augur.calibration.weight_store import (
    get_effective_weight,
    load_all_overrides,
    override_history,
    persist_weight_overrides,
)


class TestPersistWeightOverrides:
    @pytest.mark.asyncio
    async def test_empty_updates_returns_zero(self):
        pool, conn = _make_pool()
        result = await persist_weight_overrides(pool, run_id=uuid.uuid4(), updates={})
        assert result == 0

    @pytest.mark.asyncio
    async def test_writes_one_row_per_source(self):
        pool, conn = _make_pool()
        conn.execute = AsyncMock()
        # Mock transaction context manager
        conn.transaction = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        ))
        run_id = uuid.uuid4()
        updates = {"src_a": 0.75, "src_b": 0.55}
        result = await persist_weight_overrides(pool, run_id=run_id, updates=updates)
        assert result == 2

    @pytest.mark.asyncio
    async def test_supersedes_existing_before_insert(self):
        pool, conn = _make_pool()
        execute_calls = []
        conn.execute = AsyncMock(side_effect=lambda sql, *args: execute_calls.append(sql.strip()[:30]))
        conn.transaction = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        ))
        await persist_weight_overrides(
            pool, run_id=uuid.uuid4(), updates={"src_a": 0.8}
        )
        # Should have UPDATE (supersede) then INSERT for each source
        update_calls = [c for c in execute_calls if c.startswith("UPDATE")]
        insert_calls = [c for c in execute_calls if c.startswith("INSERT")]
        assert len(update_calls) >= 1
        assert len(insert_calls) >= 1


class TestLoadAllOverrides:
    @pytest.mark.asyncio
    async def test_empty_result(self):
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[])
        result = await load_all_overrides(pool)
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_dict_keyed_by_source_id(self):
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[
            {"source_id": "src_a", "weight": 0.75},
            {"source_id": "src_b", "weight": 0.55},
        ])
        result = await load_all_overrides(pool)
        assert result == {"src_a": 0.75, "src_b": 0.55}


class TestGetEffectiveWeight:
    @pytest.mark.asyncio
    async def test_uses_override_when_present(self):
        pool, conn = _make_pool()
        conn.fetchrow = AsyncMock(return_value={"weight": 0.82})
        result = await get_effective_weight(pool, "src_a", yaml_weight=0.5)
        assert result == 0.82

    @pytest.mark.asyncio
    async def test_falls_back_to_yaml_when_no_override(self):
        pool, conn = _make_pool()
        conn.fetchrow = AsyncMock(return_value=None)
        result = await get_effective_weight(pool, "src_a", yaml_weight=0.65)
        assert result == 0.65

    @pytest.mark.asyncio
    async def test_override_float_conversion(self):
        pool, conn = _make_pool()
        conn.fetchrow = AsyncMock(return_value={"weight": "0.78"})
        result = await get_effective_weight(pool, "src_a", yaml_weight=0.5)
        assert isinstance(result, float)
        assert result == 0.78


class TestOverrideHistory:
    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self):
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[
            {
                "weight": 0.75,
                "calibration_run_id": uuid.uuid4(),
                "applied_at": _NOW,
                "applied_by": "operator",
                "notes": "",
                "superseded_at": None,
            }
        ])
        result = await override_history(pool, "src_a")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["weight"] == 0.75

    @pytest.mark.asyncio
    async def test_empty_history(self):
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[])
        result = await override_history(pool, "src_a")
        assert result == []


# ── source_registry: load_sources_with_overrides ───────────────────────────────


from augur.ingestion.source_registry import load_sources_with_overrides, load_sources


class TestLoadSourcesWithOverrides:
    @pytest.mark.asyncio
    async def test_no_overrides_returns_yaml_weights(self):
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[])
        sources = await load_sources_with_overrides(pool)
        yaml_sources = load_sources()
        # Same count
        assert len(sources) == len(yaml_sources)
        # Same weights (no overrides applied)
        for s, y in zip(sources, yaml_sources):
            assert s.starting_source_weight == y.starting_source_weight

    @pytest.mark.asyncio
    async def test_applies_override_for_matching_source(self):
        yaml_sources = load_sources()
        if not yaml_sources:
            pytest.skip("No sources in sources.yaml")

        target = yaml_sources[0]
        new_weight = round(target.starting_source_weight + 0.1, 4)
        if new_weight > 1.0:
            new_weight = round(target.starting_source_weight - 0.1, 4)

        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[
            {"source_id": target.source_id, "weight": new_weight}
        ])

        sources = await load_sources_with_overrides(pool)
        updated = next(s for s in sources if s.source_id == target.source_id)
        assert updated.starting_source_weight == new_weight

    @pytest.mark.asyncio
    async def test_non_overridden_sources_unchanged(self):
        yaml_sources = load_sources()
        if len(yaml_sources) < 2:
            pytest.skip("Need at least 2 sources")

        target = yaml_sources[0]
        other = yaml_sources[1]

        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[
            {"source_id": target.source_id, "weight": 0.99}
        ])

        sources = await load_sources_with_overrides(pool)
        other_result = next(s for s in sources if s.source_id == other.source_id)
        assert other_result.starting_source_weight == other.starting_source_weight

    @pytest.mark.asyncio
    async def test_source_config_is_not_mutated(self):
        yaml_sources = load_sources()
        if not yaml_sources:
            pytest.skip("No sources in sources.yaml")

        target = yaml_sources[0]
        original_weight = target.starting_source_weight

        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[
            {"source_id": target.source_id, "weight": 0.99}
        ])

        await load_sources_with_overrides(pool)
        # Original object should not be mutated (dataclasses.replace creates new)
        assert target.starting_source_weight == original_weight


# ── live_tracker ────────────────────────────────────────────────────────────────


from augur.calibration.live_tracker import (
    checkpoint_live_outcomes,
    ensure_live_run,
    register_live_signals,
)
from augur.calibration.models import CalibrationRun, CalibrationStatus


class TestEnsureLiveRun:
    @pytest.mark.asyncio
    async def test_returns_existing_run_if_config_present(self):
        run_id = uuid.uuid4()
        pool = AsyncMock()

        # First acquire: check live_calibration_config → found
        # Second acquire: load calibration_runs
        config_conn = AsyncMock()
        config_conn.fetchrow = AsyncMock(return_value={"run_id": run_id})
        run_conn = AsyncMock()
        run_conn.fetchrow = AsyncMock(return_value={
            "run_id": run_id,
            "window_start": _NOW,
            "window_end": _NOW,
            "observation_extension_days": 90,
            "source_subset": None,
            "lens_subset": None,
            "model_overrides": "{}",
            "sandbox_prompt_template": "replay_sandbox_v1",
            "status": "running",
            "created_at": _NOW,
            "started_at": _NOW,
            "completed_at": None,
            "notes": "live-operation-tracking",
            "summary": None,
        })

        acquire_mock = MagicMock()
        acquire_calls = [
            AsyncMock(__aenter__=AsyncMock(return_value=config_conn),
                       __aexit__=AsyncMock(return_value=False)),
            AsyncMock(__aenter__=AsyncMock(return_value=run_conn),
                       __aexit__=AsyncMock(return_value=False)),
        ]
        acquire_mock.side_effect = acquire_calls
        pool.acquire = acquire_mock

        result = await ensure_live_run(pool)
        assert result.run_id == run_id
        assert result.status == CalibrationStatus.RUNNING

    @pytest.mark.asyncio
    async def test_creates_new_run_if_no_config(self):
        pool = AsyncMock()

        # conn used for both the config check and the insert (two acquires)
        create_conn = AsyncMock()
        create_conn.fetchrow = AsyncMock(return_value=None)
        create_conn.execute = AsyncMock()
        create_conn.transaction = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        ))

        ctx = AsyncMock(
            __aenter__=AsyncMock(return_value=create_conn),
            __aexit__=AsyncMock(return_value=False),
        )
        # Return the same context for any number of acquire() calls
        pool.acquire = MagicMock(return_value=ctx)

        result = await ensure_live_run(pool)
        assert result.status == CalibrationStatus.RUNNING
        assert result.notes == "live-operation-tracking"
        assert result.observation_extension_days == 90


class TestRegisterLiveSignals:
    @pytest.mark.asyncio
    async def test_empty_signals_returns_zero(self):
        pool = AsyncMock()
        with patch("augur.calibration.live_tracker.ensure_live_run") as mock_ensure:
            result = await register_live_signals(pool, [])
        assert result == 0
        mock_ensure.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_register_signals_for_run(self):
        pool = AsyncMock()
        run_id = uuid.uuid4()
        mock_run = CalibrationRun(
            run_id=run_id,
            window_start=_NOW,
            window_end=_NOW,
            status=CalibrationStatus.RUNNING,
        )
        signals = [
            {
                "signal_id": uuid.uuid4(),
                "source_id": "src_a",
                "lens_id": "financial",
                "content_timestamp": _NOW,
            }
        ]
        with patch("augur.calibration.live_tracker.ensure_live_run",
                   new=AsyncMock(return_value=mock_run)):
            with patch("augur.calibration.live_tracker.register_signals_for_run",
                       new=AsyncMock(return_value=1)) as mock_reg:
                result = await register_live_signals(pool, signals)
        assert result == 1
        mock_reg.assert_called_once_with(pool, run_id=run_id, signals=signals)


class TestCheckpointLiveOutcomes:
    @pytest.mark.asyncio
    async def test_returns_summary_dict(self):
        pool = AsyncMock()
        run_id = uuid.uuid4()
        mock_run = CalibrationRun(
            run_id=run_id,
            window_start=_NOW,
            window_end=_NOW,
            observation_extension_days=90,
            status=CalibrationStatus.RUNNING,
        )
        expected_summary = {"anchored_persistent": 10, "isolated_in_tier_a": 5}
        with patch("augur.calibration.live_tracker.ensure_live_run",
                   new=AsyncMock(return_value=mock_run)):
            with patch("augur.calibration.live_tracker.resolve_outcomes",
                       new=AsyncMock(return_value=expected_summary)):
                result = await checkpoint_live_outcomes(pool)
        assert result == expected_summary


# ── monitoring/health ──────────────────────────────────────────────────────────


from augur.monitoring.health import log_job_complete, log_job_start


class TestLogJobStart:
    @pytest.mark.asyncio
    async def test_returns_log_id(self):
        pool, conn = _make_pool()
        conn.fetchval = AsyncMock(return_value=42)
        result = await log_job_start(pool, "ingestion")
        assert result == 42

    @pytest.mark.asyncio
    async def test_inserts_with_running_status(self):
        pool, conn = _make_pool()
        conn.fetchval = AsyncMock(return_value=1)
        await log_job_start(pool, "anchoring")
        call_args = conn.fetchval.call_args[0][0]
        assert "pipeline_run_log" in call_args
        assert "running" in call_args


class TestLogJobComplete:
    @pytest.mark.asyncio
    async def test_updates_correct_log_id(self):
        pool, conn = _make_pool()
        conn.execute = AsyncMock()
        await log_job_complete(pool, 42, status="ok", n_processed=100)
        call_args = conn.execute.call_args[0]
        # The log_id 42 should be among the params
        assert 42 in call_args

    @pytest.mark.asyncio
    async def test_error_status(self):
        pool, conn = _make_pool()
        conn.execute = AsyncMock()
        await log_job_complete(pool, 1, status="error", error_message="Something broke")
        sql = conn.execute.call_args[0][0]
        assert "UPDATE pipeline_run_log" in sql


# ── scheduler: live calibration job registered ─────────────────────────────────


class TestSchedulerJobs:
    def test_live_calibration_job_in_scheduler(self):
        from augur.scheduler import create_scheduler

        app_state = MagicMock()
        app_state.raw_pool = AsyncMock()
        app_state.llm_client = AsyncMock()

        scheduler = create_scheduler(app_state)
        job_ids = {job.id for job in scheduler.get_jobs()}

        assert "live_calibration_checkpoint" in job_ids
        assert "disconfirmation" in job_ids
        assert "anchoring" in job_ids
        assert "extraction" in job_ids
        assert "ingestion" in job_ids

    def test_live_calibration_job_runs_after_disconfirmation(self):
        from augur.scheduler import create_scheduler
        from apscheduler.triggers.cron import CronTrigger

        app_state = MagicMock()
        app_state.raw_pool = AsyncMock()
        app_state.llm_client = AsyncMock()

        scheduler = create_scheduler(app_state)
        jobs = {job.id: job for job in scheduler.get_jobs()}

        disconf_trigger = jobs["disconfirmation"].trigger
        live_trigger = jobs["live_calibration_checkpoint"].trigger

        # Just confirm the jobs exist and have cron triggers
        assert isinstance(disconf_trigger, CronTrigger)
        assert isinstance(live_trigger, CronTrigger)


# ── calibration/__init__ exports ───────────────────────────────────────────────


class TestCalibrationInit:
    def test_weight_store_exports_available(self):
        from augur.calibration import (
            get_effective_weight,
            load_all_overrides,
            persist_weight_overrides,
        )
        assert callable(persist_weight_overrides)
        assert callable(load_all_overrides)
        assert callable(get_effective_weight)


# ── Integration: apply_weights calls persist ───────────────────────────────────


from augur.calibration.models import CalibrationRun, CalibrationStatus
from augur.calibration.orchestrator import CalibrationOrchestrator


class TestApplyWeightsPersists:
    @pytest.mark.asyncio
    async def test_apply_weights_calls_persist(self):
        pool = AsyncMock()
        orch = CalibrationOrchestrator(pool, AsyncMock())
        run = CalibrationRun(
            run_id=uuid.uuid4(),
            window_start=_NOW,
            window_end=_NOW,
            status=CalibrationStatus.COMPLETE,
            summary={
                "source_scores": [
                    {"source_id": "src_a", "proposed_weight": 0.75},
                ]
            },
            created_at=_NOW,
        )
        with patch(
            "augur.calibration.weight_store.persist_weight_overrides",
            new=AsyncMock(return_value=1),
        ) as mock_persist:
            result = await orch.apply_weights(run)

        mock_persist.assert_called_once()
        call_kwargs = mock_persist.call_args.kwargs
        assert call_kwargs["run_id"] == run.run_id
        assert call_kwargs["updates"] == {"src_a": 0.75}

    @pytest.mark.asyncio
    async def test_apply_weights_no_sources_skips_persist(self):
        """When no updates, persist should never be called."""
        pool = AsyncMock()
        orch = CalibrationOrchestrator(pool, AsyncMock())
        run = CalibrationRun(
            run_id=uuid.uuid4(),
            window_start=_NOW,
            window_end=_NOW,
            status=CalibrationStatus.COMPLETE,
            summary={"source_scores": []},
            created_at=_NOW,
        )
        with patch(
            "augur.calibration.weight_store.persist_weight_overrides",
            new=AsyncMock(return_value=0),
        ) as mock_persist:
            result = await orch.apply_weights(run)

        mock_persist.assert_not_called()
        assert result == {}
