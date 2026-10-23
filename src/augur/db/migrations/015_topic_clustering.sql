-- 015: Topics from graph clustering (docs/augur-presentation.md, "How topics emerge")
--
-- A topic is created by the operator or by clustering; each membership row
-- says which, so clustering never removes a member the operator added.
-- A hidden topic stays in the table (clustering does not recreate it) but
-- leaves every read path, Discourse included.

ALTER TABLE topics
    ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'operator'
        CHECK (origin IN ('operator', 'clustered')),
    ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS clustered_at TIMESTAMPTZ;

ALTER TABLE topic_nodes
    ADD COLUMN IF NOT EXISTS added_by TEXT NOT NULL DEFAULT 'operator'
        CHECK (added_by IN ('operator', 'clustering'));

-- Same columns, hidden topics left out (docs/augur-architecture.md, "Sibling systems")
CREATE OR REPLACE VIEW core_bridge.topics AS
SELECT t.topic_id,
       t.name,
       t.description,
       t.dimension,
       t.created_at
  FROM topics t
 WHERE NOT t.hidden;

CREATE OR REPLACE VIEW core_bridge.topic_nodes AS
SELECT tn.topic_id,
       tn.node_id,
       n.node_type::text AS node_type,
       n.name            AS node_name,
       tn.added_at
  FROM topic_nodes tn
  JOIN nodes n ON n.node_id = tn.node_id
  JOIN topics t ON t.topic_id = tn.topic_id
 WHERE NOT t.hidden;
