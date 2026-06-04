-- Phase 7: Live operation infrastructure
-- Source weight overrides (calibration results applied by operator)
-- Pipeline run log for operational monitoring

CREATE TABLE IF NOT EXISTS source_weight_overrides (
    override_id     BIGSERIAL PRIMARY KEY,
    source_id       TEXT        NOT NULL,
    weight          FLOAT       NOT NULL CHECK (weight >= 0.0 AND weight <= 1.0),
    calibration_run_id UUID     REFERENCES calibration_runs(run_id),
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    applied_by      TEXT        NOT NULL DEFAULT 'operator',
    notes           TEXT        NOT NULL DEFAULT '',
    superseded_at   TIMESTAMPTZ          -- set when a newer override replaces this one
);

CREATE INDEX IF NOT EXISTS idx_source_weight_overrides_source
    ON source_weight_overrides (source_id, applied_at DESC);

-- Pipeline run log: one row per scheduled job execution
CREATE TABLE IF NOT EXISTS pipeline_run_log (
    log_id          BIGSERIAL   PRIMARY KEY,
    job_name        TEXT        NOT NULL,  -- 'ingestion' | 'extraction' | 'anchoring' | 'disconfirmation' | 'live_calibration_checkpoint'
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    status          TEXT        NOT NULL DEFAULT 'running', -- 'running' | 'ok' | 'error'
    n_processed     INT         NOT NULL DEFAULT 0,
    n_errors        INT         NOT NULL DEFAULT 0,
    error_message   TEXT,
    metadata        JSONB       NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_pipeline_run_log_job_started
    ON pipeline_run_log (job_name, started_at DESC);

-- Live calibration run tracking: links live-operation signals to an ongoing run
-- (not a replay run; just a continuously-open calibration_run in status=running)
-- The run_id is stored in a single-row config table for easy lookup
CREATE TABLE IF NOT EXISTS live_calibration_config (
    singleton       BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
    run_id          UUID    REFERENCES calibration_runs(run_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
