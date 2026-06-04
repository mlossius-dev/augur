-- Phase 11: Conversation layer
-- Stores user question/answer sessions grounded in graph evidence.
-- Sessions are ephemeral by design (max 2 hours active); older ones are
-- cleaned up by the scheduler.

CREATE TABLE IF NOT EXISTS conversation_sessions (
    session_id  UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB       NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   UUID        NOT NULL REFERENCES conversation_sessions(session_id) ON DELETE CASCADE,
    role         TEXT        NOT NULL CHECK (role IN ('user', 'assistant')),
    content      TEXT        NOT NULL,
    context_node_ids UUID[]  NOT NULL DEFAULT '{}',   -- graph nodes cited
    context_edge_ids UUID[]  NOT NULL DEFAULT '{}',   -- graph edges cited
    model_used   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conv_messages_session
    ON conversation_messages (session_id, created_at);

-- Prune old sessions (run from scheduler weekly)
CREATE OR REPLACE FUNCTION prune_old_sessions(max_age_hours INT DEFAULT 48) RETURNS INT AS $$
DECLARE
    deleted INT;
BEGIN
    DELETE FROM conversation_sessions
    WHERE last_active < now() - (max_age_hours || ' hours')::interval;
    GET DIAGNOSTICS deleted = ROW_COUNT;
    RETURN deleted;
END;
$$ LANGUAGE plpgsql;
