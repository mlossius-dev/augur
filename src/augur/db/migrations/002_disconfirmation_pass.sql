-- Phase 5: Disconfirmation pass infrastructure
--
-- Adds:
--   1. disconfirmation_pass_events table — records every challenge attempt,
--      including "no disconfirmation found" outcomes (which are not graph
--      mutations and therefore don't appear in graph_update_events).
--   2. Index on edges.last_disconfirmation_pass for efficient edge selection.
--
-- The "disconfirmation found" path (edge weakened / signal added) flows
-- through the Applier and into graph_update_events with source='disconfirmation'.
-- The "no disconfirmation found" path is recorded here only.

CREATE TABLE IF NOT EXISTS disconfirmation_pass_events (
    pass_event_id       UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The edge that was challenged
    edge_id             UUID        NOT NULL REFERENCES edges(edge_id),
    -- When the challenge ran
    challenged_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Outcome: found | not_found | error
    outcome             TEXT        NOT NULL,
    -- LLM reasoning summary
    reasoning           TEXT,
    -- The window of signals reviewed during the challenge
    signals_reviewed    UUID[]      NOT NULL DEFAULT '{}',
    -- Langfuse trace ID for the challenge LLM call
    langfuse_trace_id   TEXT,
    -- Weight band at the time of the challenge (for historical audit)
    weight_band_at_challenge TEXT
);

CREATE INDEX IF NOT EXISTS idx_dpe_edge ON disconfirmation_pass_events (edge_id);
CREATE INDEX IF NOT EXISTS idx_dpe_challenged_at ON disconfirmation_pass_events (challenged_at DESC);

-- Efficient selection of edges by recency of challenge
CREATE INDEX IF NOT EXISTS idx_edges_last_disconf ON edges (last_disconfirmation_pass NULLS FIRST);
