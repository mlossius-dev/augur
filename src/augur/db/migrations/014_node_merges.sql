-- 014: Node merges (docs/augur-graph-schema.md, "Merging duplicate nodes")
--
-- A merged-away node is kept as a tombstone pointing to its survivor, never
-- deleted: read paths exclude it and lookups by its ID resolve to the survivor.

ALTER TABLE nodes
    ADD COLUMN IF NOT EXISTS merged_into UUID REFERENCES nodes(node_id),
    ADD COLUMN IF NOT EXISTS merged_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_nodes_merged_into ON nodes (merged_into)
    WHERE merged_into IS NOT NULL;

-- core_bridge.events lists nodes directly; tombstones leave it. Same columns,
-- so the published shape is unchanged (docs/augur-architecture.md, "Sibling systems").
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
 WHERE n.node_type = 'event'
   AND n.merged_into IS NULL;
