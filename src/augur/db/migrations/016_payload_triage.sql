-- 016: Payload triage (docs/augur-signal-pipeline.md, "Which payloads are read")
--
-- A decision model's answers about each live news payload: which lenses are
-- relevant, how consequential, whether a slow-building risk, whether soft
-- news. Extraction orders its reading and picks lenses from these. The call
-- itself is in llm_calls; this keeps the answers where selection reads them.

CREATE TABLE IF NOT EXISTS payload_triage (
    payload_id  UUID PRIMARY KEY REFERENCES payloads(payload_id) ON DELETE CASCADE,
    model       TEXT        NOT NULL,
    answers     JSONB       NOT NULL,   -- question name -> probability (0..1)
    call_id     UUID        NOT NULL,   -- llm_calls.call_id
    triaged_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
