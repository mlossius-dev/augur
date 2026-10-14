-- Migration 011: llm_calls — Augur's own record of every LLM call.
--
-- Replaces Langfuse.  docs/augur-architecture.md, "Observability".
--
-- The LLM client inserts a row (status 'pending') before it calls OpenRouter
-- and completes it afterwards, so no call is ever unrecorded: if the insert
-- fails, the call is not made.  The same table feeds the daily budget cap
-- (sum of cost_usd over today's budgeted calls), so the cap holds across
-- processes and restarts.
--
-- Provenance columns that held Langfuse trace IDs are renamed to point at
-- llm_calls instead.  They stay TEXT so rows written before this migration
-- keep their old values.
--
-- Idempotent: Core's runner re-applies migrations that do not record
-- themselves.

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id             UUID        PRIMARY KEY,
    called_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    -- pending | ok | error
    status              TEXT        NOT NULL DEFAULT 'pending',
    stage               TEXT        NOT NULL,
    prompt_template_id  TEXT        NOT NULL,
    model               TEXT        NOT NULL,
    -- Free-tier calls (conversation layer) do not count against the budget
    is_free_tier        BOOLEAN     NOT NULL,
    temperature         DOUBLE PRECISION,
    -- Entity IDs and other context the caller passed (payload_id, lens_id, ...)
    metadata            JSONB       NOT NULL DEFAULT '{}',
    messages            JSONB       NOT NULL,
    output              TEXT,
    error               TEXT,
    attempts            INTEGER     NOT NULL DEFAULT 0,
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    -- As reported by OpenRouter (usage.cost); NULL when not reported
    cost_usd            NUMERIC(16, 10),
    latency_ms          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_called_at ON llm_calls (called_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_calls_stage ON llm_calls (stage, called_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_calls_template ON llm_calls (prompt_template_id, called_at DESC);
CREATE INDEX IF NOT EXISTS idx_llm_calls_status ON llm_calls (status) WHERE status <> 'ok';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'nodes' AND column_name = 'langfuse_trace_ids') THEN
        ALTER TABLE nodes RENAME COLUMN langfuse_trace_ids TO llm_call_ids;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'edges' AND column_name = 'langfuse_trace_ids') THEN
        ALTER TABLE edges RENAME COLUMN langfuse_trace_ids TO llm_call_ids;
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'disconfirmation_pass_events'
                  AND column_name = 'langfuse_trace_id') THEN
        ALTER TABLE disconfirmation_pass_events
            RENAME COLUMN langfuse_trace_id TO llm_call_id;
    END IF;
END $$;
