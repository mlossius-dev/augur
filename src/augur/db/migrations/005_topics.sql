-- Phase 9: Topic view and geographic scoping
-- Topics are operator-curated named clusters of graph nodes.
-- Geographic scoping maps lat/lon to perspective pools.

CREATE TABLE IF NOT EXISTS topics (
    topic_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    dimension   TEXT,           -- optional affiliation with one of the five dimensions
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_topics_name ON topics (name);

-- Junction table: which nodes belong to which topic
CREATE TABLE IF NOT EXISTS topic_nodes (
    topic_id    UUID        NOT NULL REFERENCES topics(topic_id) ON DELETE CASCADE,
    node_id     UUID        NOT NULL REFERENCES nodes(node_id) ON DELETE CASCADE,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes       TEXT        NOT NULL DEFAULT '',
    PRIMARY KEY (topic_id, node_id)
);

CREATE INDEX IF NOT EXISTS idx_topic_nodes_node ON topic_nodes (node_id);

-- Region scope definitions: maps geographic regions to perspective pools.
-- Seed rows inserted below; operators can add more via the CLI.
CREATE TABLE IF NOT EXISTS region_scope_definitions (
    region_id       TEXT    PRIMARY KEY,
    display_name    TEXT    NOT NULL,
    perspectives    TEXT[]  NOT NULL DEFAULT '{}',
    entity_keywords TEXT[]  NOT NULL DEFAULT '{}',
    lat_min         FLOAT,
    lat_max         FLOAT,
    lon_min         FLOAT,
    lon_max         FLOAT
);

-- Seed with regions that correspond to current perspective pools
INSERT INTO region_scope_definitions
    (region_id, display_name, perspectives, entity_keywords, lat_min, lat_max, lon_min, lon_max)
VALUES
    ('nordic', 'Nordic and Baltic', ARRAY['us_eu'],
     ARRAY['norway','sweden','denmark','finland','baltic','nordic','nok','sek','nok ','oslo','stockholm','helsinki','copenhagen'],
     54.5, 71.5, 4.0, 32.0),

    ('europe_west', 'Western Europe', ARRAY['us_eu'],
     ARRAY['europe','eu ','euro','ecb','eurozone','german','france','italy','spain','brussels','berlin'],
     35.0, 70.0, -10.0, 25.0),

    ('north_america', 'North America', ARRAY['us_eu'],
     ARRAY['united states','us ','federal reserve','fed ','dollar','nasdaq','dow jones','washington','new york'],
     24.0, 72.0, -170.0, -50.0),

    ('russia', 'Russia and Central Asia', ARRAY['russia'],
     ARRAY['russia','kremlin','ruble','gazprom','rosneft','moscow','putin','siberia'],
     40.0, 80.0, 30.0, 180.0),

    ('middle_east', 'Middle East and Gulf', ARRAY['gulf'],
     ARRAY['saudi','iran','iraq','opec','gulf','hormuz','riyadh','tehran','uae','abu dhabi','dubai'],
     15.0, 42.0, 34.0, 63.0),

    ('south_asia', 'South Asia', ARRAY['india'],
     ARRAY['india','pakistan','sri lanka','bangladesh','mumbai','delhi','rupee','rbi ','modi'],
     5.0, 37.0, 60.0, 100.0),

    ('east_asia', 'East and Southeast Asia', ARRAY['china','southeast_asia_pacific'],
     ARRAY['china','beijing','shanghai','yuan','renminbi','taiwan','japan','tokyo','south korea','asean','singapore','brics'],
     -10.0, 55.0, 95.0, 150.0),

    ('global', 'Global', ARRAY['us_eu','india','china','russia','gulf','southeast_asia_pacific'],
     ARRAY[],
     -90.0, 90.0, -180.0, 180.0)
ON CONFLICT (region_id) DO NOTHING;
