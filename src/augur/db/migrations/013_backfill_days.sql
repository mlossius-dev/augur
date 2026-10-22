-- 013: Historical backfill progress, one row per day (docs/augur-architecture.md, "Historical backfill")
--
-- The backfill runs newest day first and can stop (budget, interruption) at
-- any point; this records which days are done and what each cost, so a
-- restart continues with the day before the oldest one done.

CREATE TABLE IF NOT EXISTS backfill_days (
    day             DATE        PRIMARY KEY,
    status          TEXT        NOT NULL CHECK (status IN ('done', 'partial')),
    n_found         INTEGER     NOT NULL DEFAULT 0,  -- items published that day in the archive
    n_payloads      INTEGER     NOT NULL DEFAULT 0,  -- drawn and stored (clean)
    n_signals       INTEGER     NOT NULL DEFAULT 0,
    n_applied       INTEGER     NOT NULL DEFAULT 0,  -- graph operations applied by anchoring
    cost_usd        NUMERIC(14, 10) NOT NULL DEFAULT 0,
    by_source       JSONB       NOT NULL DEFAULT '{}',
    finished_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
