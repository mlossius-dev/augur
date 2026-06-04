-- Phase 10: Scenario projection
-- Scenarios are LLM-generated near-term forecasts derived from the current
-- graph state.  Each scenario belongs to one dimension and carries a
-- probability band, a time horizon, and links back to the graph evidence
-- that supported or contradicted it.

CREATE TABLE IF NOT EXISTS scenarios (
    scenario_id     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    dimension       TEXT,                           -- one of the five dimensions, or NULL = cross-cutting
    title           TEXT        NOT NULL,
    summary         TEXT        NOT NULL,
    probability_band TEXT        NOT NULL            -- high | moderate | low | negligible
                    CHECK (probability_band IN ('high','moderate','low','negligible')),
    time_horizon    TEXT        NOT NULL DEFAULT '3–6 months',
    key_condition_ids   UUID[]  NOT NULL DEFAULT '{}',
    supporting_edge_ids UUID[]  NOT NULL DEFAULT '{}',
    contradicting_edge_ids UUID[] NOT NULL DEFAULT '{}',
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_of           TIMESTAMPTZ NOT NULL DEFAULT now(),  -- graph snapshot time
    model_used      TEXT,
    deprecated      BOOLEAN     NOT NULL DEFAULT FALSE
);

-- Fast lookup by dimension and recency
CREATE INDEX IF NOT EXISTS idx_scenarios_dimension
    ON scenarios (dimension, generated_at DESC)
    WHERE NOT deprecated;

CREATE INDEX IF NOT EXISTS idx_scenarios_as_of
    ON scenarios (as_of DESC)
    WHERE NOT deprecated;
