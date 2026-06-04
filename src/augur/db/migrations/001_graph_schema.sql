-- Migration 001: Tier B graph schema + Tier A signal store
--
-- Implements the full data model from docs/augur-graph-schema.md and the
-- Tier A signal store from docs/augur-signal-pipeline.md.
--
-- Node storage: a unified `nodes` table with a JSONB `type_data` column for
-- type-specific fields.  Pydantic models at the application layer enforce the
-- per-type field contracts; the applier is the gate so DB-level constraints are
-- kept intentionally minimal.
--
-- Edge storage: a unified `edges` table.  `falsification_criteria` is NOT NULL
-- at the DB level — the DB enforces what the spec requires.
--
-- Append-only history tables record every weight change and every condition/
-- claim state change.  The graph at any past timestamp is reconstructed by
-- reading these tables up to the desired `content_timestamp`.

-- ── Enum types ───────────────────────────────────────────────────────────────

CREATE TYPE node_type_enum AS ENUM (
    'entity', 'condition', 'event', 'quantity', 'scenario', 'claim'
);

CREATE TYPE edge_type_enum AS ENUM (
    'causes', 'enables', 'constrains', 'accelerates',
    'correlates_with', 'contradicts', 'refines',
    'part_of', 'produces'
);

-- Five ordinal bands plus disputed; numeric anchors are for projection arithmetic only
CREATE TYPE weight_band_enum AS ENUM (
    'strong', 'moderate', 'weak', 'provisional', 'disputed'
);

CREATE TYPE confidence_band_enum AS ENUM (
    'hard_datum', 'reported_claim', 'inference', 'weak_inference'
);

CREATE TYPE condition_state_enum AS ENUM (
    'active', 'inactive', 'partially_active', 'disputed', 'unknown'
);

CREATE TYPE entity_kind_enum AS ENUM (
    'state', 'organization', 'company', 'place', 'infrastructure',
    'sector', 'commodity', 'currency', 'instrument'
);

CREATE TYPE event_kind_enum AS ENUM (
    'geopolitical', 'economic', 'physical', 'policy', 'corporate', 'natural'
);

CREATE TYPE claim_kind_enum AS ENUM (
    'factual', 'interpretive', 'contested'
);

CREATE TYPE claim_assessment_enum AS ENUM (
    'well_supported', 'partially_supported', 'contested',
    'weakly_supported', 'not_supported'
);

-- ── Tier B: Nodes ─────────────────────────────────────────────────────────────

CREATE TABLE nodes (
    node_id     UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    node_type   node_type_enum  NOT NULL,
    name        TEXT            NOT NULL,
    description TEXT,
    -- Type-specific fields (validated by Pydantic + Applier, not DB constraints)
    -- Entity:    {entity_kind, aliases: []}
    -- Condition: {current_state, current_state_confidence, subject_entities: []}
    -- Event:     {occurred_at, occurred_location, event_kind, subject_entities: []}
    -- Quantity:  {unit, time_series_reference, current_value, current_value_as_of, subject_entities: []}
    -- Scenario:  {precondition_nodes: [], projected_trajectory, created_by}
    -- Claim:     {claim_text, claim_kind, evidence_for: [], evidence_against: [],
    --             current_assessment, subject_entities: []}
    type_data   JSONB           NOT NULL DEFAULT '{}',
    -- Provenance
    created_from        UUID[]  NOT NULL DEFAULT '{}',  -- originating signal IDs
    langfuse_trace_ids  TEXT[]  NOT NULL DEFAULT '{}',
    -- Timestamps
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX idx_nodes_type ON nodes (node_type);
CREATE INDEX idx_nodes_name_trgm ON nodes USING GIN (name gin_trgm_ops);
CREATE INDEX idx_nodes_type_data ON nodes USING GIN (type_data);

-- ── Tier B: Condition state history (append-only) ─────────────────────────────

CREATE TABLE condition_state_history (
    id                  BIGSERIAL           PRIMARY KEY,
    node_id             UUID                NOT NULL REFERENCES nodes(node_id),
    new_state           condition_state_enum NOT NULL,
    previous_state      condition_state_enum,
    confidence_band     weight_band_enum,
    reasoning           TEXT,
    triggered_by        UUID[]              NOT NULL DEFAULT '{}',
    -- content_timestamp is the time the change represents (not processing time)
    content_timestamp   TIMESTAMPTZ         NOT NULL,
    recorded_at         TIMESTAMPTZ         NOT NULL DEFAULT now()
);

CREATE INDEX idx_csh_node_id ON condition_state_history (node_id, content_timestamp DESC);

-- ── Tier B: Claim assessment history (append-only) ───────────────────────────

CREATE TABLE claim_assessment_history (
    id                  BIGSERIAL               PRIMARY KEY,
    node_id             UUID                    NOT NULL REFERENCES nodes(node_id),
    new_assessment      claim_assessment_enum   NOT NULL,
    previous_assessment claim_assessment_enum,
    reasoning           TEXT,
    triggered_by        UUID[]                  NOT NULL DEFAULT '{}',
    content_timestamp   TIMESTAMPTZ             NOT NULL,
    recorded_at         TIMESTAMPTZ             NOT NULL DEFAULT now()
);

CREATE INDEX idx_cah_node_id ON claim_assessment_history (node_id, content_timestamp DESC);

-- ── Tier B: Edges ─────────────────────────────────────────────────────────────

CREATE TABLE edges (
    edge_id                 UUID                PRIMARY KEY DEFAULT gen_random_uuid(),
    source_node_id          UUID                NOT NULL REFERENCES nodes(node_id),
    target_node_id          UUID                NOT NULL REFERENCES nodes(node_id),
    edge_type               edge_type_enum      NOT NULL,
    current_weight_band     weight_band_enum    NOT NULL,
    supporting_signals      UUID[]              NOT NULL DEFAULT '{}',
    disconfirming_signals   UUID[]              NOT NULL DEFAULT '{}',
    reasoning               TEXT                NOT NULL,
    -- falsification_criteria is NOT NULL at DB level — the spec requires it
    falsification_criteria  TEXT                NOT NULL,
    last_disconfirmation_pass TIMESTAMPTZ,
    -- Provenance
    created_from            UUID[]              NOT NULL DEFAULT '{}',
    langfuse_trace_ids      TEXT[]              NOT NULL DEFAULT '{}',
    -- Timestamps
    created_at              TIMESTAMPTZ         NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ         NOT NULL DEFAULT now(),
    deprecated              BOOLEAN             NOT NULL DEFAULT FALSE,
    deprecated_at           TIMESTAMPTZ
);

CREATE INDEX idx_edges_source ON edges (source_node_id);
CREATE INDEX idx_edges_target ON edges (target_node_id);
CREATE INDEX idx_edges_type ON edges (edge_type);
CREATE INDEX idx_edges_weight ON edges (current_weight_band) WHERE NOT deprecated;

-- ── Tier B: Edge weight history (append-only) ─────────────────────────────────

CREATE TABLE edge_weight_history (
    id                      BIGSERIAL           PRIMARY KEY,
    edge_id                 UUID                NOT NULL REFERENCES edges(edge_id),
    weight_band             weight_band_enum    NOT NULL,
    previous_weight_band    weight_band_enum,
    -- change_type: initial | strengthened | weakened | disputed | disconfirmation | operator_override
    change_type             TEXT                NOT NULL,
    reasoning               TEXT                NOT NULL,
    triggered_by            UUID[]              NOT NULL DEFAULT '{}',  -- signal IDs
    content_timestamp       TIMESTAMPTZ         NOT NULL,
    recorded_at             TIMESTAMPTZ         NOT NULL DEFAULT now()
);

CREATE INDEX idx_ewh_edge_id ON edge_weight_history (edge_id, content_timestamp DESC);

-- ── Tier B: Graph update events (immutable mutation log) ─────────────────────

CREATE TABLE graph_update_events (
    event_id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type          TEXT        NOT NULL,
    target_node_id      UUID        REFERENCES nodes(node_id),
    target_edge_id      UUID        REFERENCES edges(edge_id),
    -- Full operation as submitted to the Applier
    operation_data      JSONB       NOT NULL,
    triggered_by        UUID[]      NOT NULL DEFAULT '{}',
    reasoning           TEXT,
    confidence          TEXT,
    -- content_timestamp from the originating signal; used for replay mode
    content_timestamp   TIMESTAMPTZ NOT NULL,
    applied_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- source: anchoring | disconfirmation | operator_override | seed
    source              TEXT        NOT NULL DEFAULT 'anchoring',
    rejected            BOOLEAN     NOT NULL DEFAULT FALSE,
    rejection_reason    TEXT
);

CREATE INDEX idx_gue_content_ts ON graph_update_events (content_timestamp DESC);
CREATE INDEX idx_gue_node ON graph_update_events (target_node_id) WHERE target_node_id IS NOT NULL;
CREATE INDEX idx_gue_edge ON graph_update_events (target_edge_id) WHERE target_edge_id IS NOT NULL;

-- ── Entity alias table ────────────────────────────────────────────────────────

CREATE TABLE aliases (
    alias_id            BIGSERIAL   PRIMARY KEY,
    alias_text          TEXT        NOT NULL,
    -- canonical_node_id is NULL for seed aliases before the node is created
    canonical_node_id   UUID        REFERENCES nodes(node_id),
    canonical_name      TEXT        NOT NULL,
    added_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- added_by: seed | operator | applier
    added_by            TEXT        NOT NULL DEFAULT 'seed',
    UNIQUE(alias_text)
);

-- Trigram index for fuzzy alias matching during entity resolution
CREATE INDEX idx_aliases_text_trgm ON aliases USING GIN (alias_text gin_trgm_ops);

-- ── Tier A: Raw payload archive ───────────────────────────────────────────────

CREATE TABLE payloads (
    payload_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           TEXT        NOT NULL,
    -- fetched_at: when Augur pulled the content (operational; not for replay mode)
    fetched_at          TIMESTAMPTZ NOT NULL,
    -- content_timestamp: when the content represents (publication date, observation date)
    -- All downstream signal timestamps inherit this value.
    content_timestamp   TIMESTAMPTZ NOT NULL,
    perspective         TEXT        NOT NULL,
    content             TEXT        NOT NULL,
    content_type        TEXT        NOT NULL DEFAULT 'article',
    language            TEXT,
    metadata            JSONB       NOT NULL DEFAULT '{}',
    archived_path       TEXT,
    rejected            BOOLEAN     NOT NULL DEFAULT FALSE,
    rejected_reason     TEXT
);

CREATE INDEX idx_payloads_source ON payloads (source_id, content_timestamp DESC);
CREATE INDEX idx_payloads_perspective ON payloads (perspective, content_timestamp DESC);

-- ── Tier A: Signal store ─────────────────────────────────────────────────────

-- claim_vector dimensions: 1536 (OpenAI text-embedding-3-small / ada-002 compatible).
-- This matches the default embedding model target for Phase 2.  If a different model
-- is chosen then this dimension must be changed and the index rebuilt.
CREATE TABLE signals (
    signal_id           UUID                    PRIMARY KEY DEFAULT gen_random_uuid(),
    payload_id          UUID                    NOT NULL REFERENCES payloads(payload_id),
    lens_id             TEXT                    NOT NULL,
    lens_version        TEXT                    NOT NULL DEFAULT '1',
    claim_text          TEXT                    NOT NULL,
    claim_vector        VECTOR(1536),
    confidence_band     confidence_band_enum    NOT NULL,
    -- Structured LLM output; validated by Pydantic at extraction time
    proposed_anchors    JSONB                   NOT NULL DEFAULT '[]',
    reasoning           TEXT,
    -- Inherited from payload.content_timestamp; drives all downstream timestamps
    content_timestamp   TIMESTAMPTZ             NOT NULL,
    extracted_at        TIMESTAMPTZ             NOT NULL DEFAULT now(),
    -- Tier A clustering fields (populated by Stage 3)
    cluster_id          UUID,
    cluster_strength    FLOAT,
    anchored            BOOLEAN                 NOT NULL DEFAULT FALSE,
    archived_at         TIMESTAMPTZ
);

CREATE INDEX idx_signals_payload ON signals (payload_id);
CREATE INDEX idx_signals_lens ON signals (lens_id, content_timestamp DESC);
CREATE INDEX idx_signals_cluster ON signals (cluster_id) WHERE cluster_id IS NOT NULL;
CREATE INDEX idx_signals_anchored ON signals (anchored, content_timestamp DESC);
-- IVFFlat index for approximate nearest-neighbour search at Phase 2 scale
-- (100 lists is appropriate for up to ~1M vectors)
CREATE INDEX idx_signals_vector ON signals USING ivfflat (claim_vector vector_cosine_ops)
    WITH (lists = 100)
    WHERE claim_vector IS NOT NULL;

-- ── Apache AGE: graph layer ───────────────────────────────────────────────────

-- Create the AGE graph that mirrors the Tier B Postgres data.
-- The Applier writes to both the Postgres tables above and this graph.
-- Cypher queries (graph traversal, projection) operate against this graph.

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

SELECT create_graph('augur_graph');

-- Pre-create vertex labels (one per node type)
SELECT create_vlabel('augur_graph', 'Entity');
SELECT create_vlabel('augur_graph', 'Condition');
SELECT create_vlabel('augur_graph', 'Event');
SELECT create_vlabel('augur_graph', 'Quantity');
SELECT create_vlabel('augur_graph', 'Scenario');
SELECT create_vlabel('augur_graph', 'Claim');

-- Pre-create edge labels (one per edge type)
SELECT create_elabel('augur_graph', 'causes');
SELECT create_elabel('augur_graph', 'enables');
SELECT create_elabel('augur_graph', 'constrains');
SELECT create_elabel('augur_graph', 'accelerates');
SELECT create_elabel('augur_graph', 'correlates_with');
SELECT create_elabel('augur_graph', 'contradicts');
SELECT create_elabel('augur_graph', 'refines');
SELECT create_elabel('augur_graph', 'part_of');
SELECT create_elabel('augur_graph', 'produces');

-- Record migration
INSERT INTO schema_migrations (version, description)
VALUES ('001', 'Graph schema: nodes, edges, weight history, signals, payloads, AGE graph')
ON CONFLICT (version) DO NOTHING;
