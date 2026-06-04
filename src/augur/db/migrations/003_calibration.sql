-- Phase 6: Calibration run infrastructure
--
-- Tables:
--   calibration_runs         : one row per calibration run (config + status)
--   signal_outcome_tracking  : one row per signal per calibration run
--                              (updated as the outcome resolves)
--
-- Signal outcomes are scored ~60-90 days after the signal's content_timestamp
-- by looking at whether the graph edges the signal contributed to were
-- subsequently strengthened, weakened, deprecated, or never anchored.

CREATE TABLE IF NOT EXISTS calibration_runs (
    run_id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Replay window
    window_start        TIMESTAMPTZ NOT NULL,
    window_end          TIMESTAMPTZ NOT NULL,
    -- How long after window_end to track signal survival (days)
    observation_extension_days INT NOT NULL DEFAULT 90,
    -- Optional filters; NULL means all
    source_subset       TEXT[],
    lens_subset         TEXT[],
    -- Model override JSON: {"extraction": "model-id", ...}
    model_overrides     JSONB       NOT NULL DEFAULT '{}',
    -- Sandbox prompt template ID used for replay extraction calls
    sandbox_prompt_template TEXT    NOT NULL DEFAULT 'replay_sandbox_v1',
    -- Status: configured | running | scoring | complete | failed
    status              TEXT        NOT NULL DEFAULT 'configured',
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    -- Free-text notes from the operator
    notes               TEXT        NOT NULL DEFAULT '',
    -- Summary JSON written at completion: source_scores, lens_scores, leakage_rate
    summary             JSONB
);

CREATE TABLE IF NOT EXISTS signal_outcome_tracking (
    tracking_id         BIGSERIAL   PRIMARY KEY,
    run_id              UUID        NOT NULL REFERENCES calibration_runs(run_id),
    signal_id           UUID        NOT NULL,
    source_id           TEXT        NOT NULL,
    lens_id             TEXT        NOT NULL,
    -- content_timestamp of the originating signal
    content_timestamp   TIMESTAMPTZ NOT NULL,
    -- Outcome: see CalibrationOutcome enum
    -- anchored_strengthened | anchored_persistent | anchored_weakened |
    -- anchored_deprecated | clustered_but_not_anchored |
    -- isolated_in_tier_a | extraction_rejected | pending
    outcome             TEXT        NOT NULL DEFAULT 'pending',
    -- Score contribution (set when outcome resolves)
    score               FLOAT,
    -- Resolved at timestamp
    resolved_at         TIMESTAMPTZ,
    -- Edge ID the signal contributed to (if anchored)
    contributed_edge_id UUID,

    UNIQUE (run_id, signal_id)
);

CREATE INDEX IF NOT EXISTS idx_sot_run ON signal_outcome_tracking (run_id);
CREATE INDEX IF NOT EXISTS idx_sot_signal ON signal_outcome_tracking (signal_id);
CREATE INDEX IF NOT EXISTS idx_sot_source ON signal_outcome_tracking (run_id, source_id);
CREATE INDEX IF NOT EXISTS idx_sot_lens ON signal_outcome_tracking (run_id, lens_id);
CREATE INDEX IF NOT EXISTS idx_sot_outcome ON signal_outcome_tracking (run_id, outcome);
CREATE INDEX IF NOT EXISTS idx_sot_pending ON signal_outcome_tracking (run_id, content_timestamp)
    WHERE outcome = 'pending';

CREATE INDEX IF NOT EXISTS idx_calibration_runs_status ON calibration_runs (status);
