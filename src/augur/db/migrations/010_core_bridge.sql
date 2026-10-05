-- Migration 010: core_bridge — the read-only contract with Augur Discourse.
--
-- docs/augur-architecture.md, "Sibling systems".  Views only.  The views run
-- with their owner's rights, so the `discourse` role reads them without any
-- grant on the tables behind them.  Enum columns are exposed as text so the
-- contract does not depend on Core's enum types.
--
-- Table names are deliberately unqualified.  Core's migrations run with
-- search_path = ag_catalog, "$user", public, so Core's tables are created in
-- ag_catalog, not public.  A view binds to the tables its names resolve to
-- when it is created, so unqualified names follow the tables wherever the
-- runner put them.
--
-- Idempotent: Core's runner re-applies migrations that do not record
-- themselves.  CREATE OR REPLACE VIEW only allows appending columns, which is
-- exactly the backward-compatible change the contract permits.

CREATE SCHEMA IF NOT EXISTS core_bridge;

-- ── Vocabulary ───────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW core_bridge.aliases AS
SELECT a.alias_text,
       a.canonical_name,
       a.canonical_node_id,
       a.added_at
  FROM aliases a;

CREATE OR REPLACE VIEW core_bridge.topics AS
SELECT t.topic_id,
       t.name,
       t.description,
       t.dimension,
       t.created_at
  FROM topics t;

CREATE OR REPLACE VIEW core_bridge.topic_nodes AS
SELECT tn.topic_id,
       tn.node_id,
       n.node_type::text AS node_type,
       n.name            AS node_name,
       tn.added_at
  FROM topic_nodes tn
  JOIN nodes n ON n.node_id = tn.node_id;

-- ── Picture of the world ─────────────────────────────────────────────────────

-- Event nodes.  occurred_at comes from type_data and is NULL when absent or
-- not an ISO date, rather than failing every read of the view.
CREATE OR REPLACE VIEW core_bridge.events AS
SELECT n.node_id,
       n.name,
       CASE WHEN n.type_data->>'occurred_at' ~ '^\d{4}-\d{2}-\d{2}'
            THEN (n.type_data->>'occurred_at')::timestamptz END AS occurred_at,
       n.type_data->>'event_kind'                               AS event_kind,
       COALESCE(n.type_data->'subject_entities', '[]'::jsonb)   AS subject_entities,
       n.type_data->>'occurred_location'                        AS occurred_location,
       n.created_at
  FROM nodes n
 WHERE n.node_type = 'event';

CREATE OR REPLACE VIEW core_bridge.condition_history AS
SELECT h.id                     AS history_id,
       h.node_id,
       n.name                   AS condition_name,
       h.new_state::text        AS new_state,
       h.previous_state::text   AS previous_state,
       h.content_timestamp,
       h.recorded_at
  FROM condition_state_history h
  JOIN nodes n ON n.node_id = h.node_id;

-- Market and macro series.  Today these live in the metadata of FRED and
-- Yahoo payloads (structured_feed_entry); this view normalizes them.  One
-- point per series per content_timestamp: the latest fetch wins.
CREATE OR REPLACE VIEW core_bridge.quantity_series AS
SELECT DISTINCT ON (p.metadata->>'symbol', p.content_timestamp)
       p.metadata->>'symbol'                              AS series_key,
       COALESCE(p.metadata->>'label', p.metadata->>'symbol') AS label,
       p.metadata->>'unit'                                AS unit,
       p.content_timestamp                                AS ts,
       (p.metadata->>'latest_value')::double precision    AS value,
       p.source_id,
       p.payload_id
  FROM payloads p
 WHERE p.content_type = 'structured_feed_entry'
   AND NOT p.rejected
   AND p.metadata ? 'symbol'
   AND p.metadata->>'latest_value' ~ '^-?\d+(\.\d+)?([eE][-+]?\d+)?$'
 ORDER BY p.metadata->>'symbol', p.content_timestamp, p.fetched_at DESC;

-- Every edge, live or deprecated, with its current band.  as_of readers use
-- created_at/deprecated_at for existence and edge_weight_history for bands.
CREATE OR REPLACE VIEW core_bridge.edges_summary AS
SELECT e.edge_id,
       e.source_node_id,
       s.name                       AS source_name,
       e.target_node_id,
       t.name                       AS target_name,
       e.edge_type::text            AS edge_type,
       e.current_weight_band::text  AS current_weight_band,
       e.deprecated,
       e.deprecated_at,
       e.created_at
  FROM edges e
  JOIN nodes s ON s.node_id = e.source_node_id
  JOIN nodes t ON t.node_id = e.target_node_id;

CREATE OR REPLACE VIEW core_bridge.edge_weight_history AS
SELECT h.id                          AS history_id,
       h.edge_id,
       h.weight_band::text           AS weight_band,
       h.previous_weight_band::text  AS previous_weight_band,
       h.content_timestamp,
       h.recorded_at
  FROM edge_weight_history h;

-- News payload metadata only (no content), so Discourse can count news
-- attention without fetching the same articles twice.
CREATE OR REPLACE VIEW core_bridge.payload_index AS
SELECT p.payload_id,
       p.source_id,
       p.perspective,
       p.language,
       p.metadata->>'headline'  AS title,
       p.metadata->>'url'       AS url,
       p.content_timestamp,
       p.fetched_at
  FROM payloads p
 WHERE p.content_type = 'article'
   AND NOT p.rejected;

-- ── Grants ───────────────────────────────────────────────────────────────────
-- Only if the role exists; Discourse's ops/bootstrap_role.sql grants the same
-- when it runs after this migration.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'discourse') THEN
        GRANT USAGE ON SCHEMA core_bridge TO discourse;
        GRANT SELECT ON ALL TABLES IN SCHEMA core_bridge TO discourse;
    END IF;
END $$;
