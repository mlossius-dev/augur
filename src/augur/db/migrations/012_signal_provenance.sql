-- 012: Signal provenance (docs/augur-architecture.md, "Historical backfill")
--
-- Which model extracted a signal, and whether it came from live ingestion or
-- the historical backfill, so backfilled material can be weighted, compared
-- or removed later.  Existing rows are live; their model is in llm_calls.

ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS extracted_by TEXT,
    ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'live'
        CHECK (origin IN ('live', 'backfill'));

CREATE INDEX IF NOT EXISTS idx_signals_origin ON signals (origin) WHERE origin <> 'live';
