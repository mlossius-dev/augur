-- Migration 000: extension verification
--
-- Extensions are created by the Docker postgres init script
-- (docker/postgres/init.sql).  This migration simply verifies they are
-- present and records the schema baseline so the application can check
-- which migrations have run.
--
-- Phase 1 will add the full Augur data model.  Phase 0 only needs the
-- infrastructure to be functional and verifiable.

-- Migration tracking table
CREATE TABLE IF NOT EXISTS schema_migrations (
    id           SERIAL       PRIMARY KEY,
    version      TEXT         NOT NULL UNIQUE,
    description  TEXT         NOT NULL,
    applied_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Verify all required extensions are present
DO $$
DECLARE
    missing TEXT[] := '{}';
    ext TEXT;
BEGIN
    FOREACH ext IN ARRAY ARRAY['vector', 'pg_trgm', 'postgis', 'age'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = ext) THEN
            missing := missing || ext;
        END IF;
    END LOOP;
    IF array_length(missing, 1) > 0 THEN
        RAISE EXCEPTION
            'Required Postgres extensions are missing: %. '
            'Rebuild the database container from docker/postgres/Dockerfile.',
            array_to_string(missing, ', ');
    END IF;
END $$;

-- Smoke-test pgvector
DO $$
BEGIN
    PERFORM '[1, 2, 3]'::vector;
    RAISE NOTICE 'pgvector: ok';
END $$;

-- Smoke-test pg_trgm
DO $$
BEGIN
    PERFORM similarity('augur', 'auger');
    RAISE NOTICE 'pg_trgm: ok';
END $$;

-- Smoke-test PostGIS
DO $$
BEGIN
    PERFORM ST_AsText(ST_Point(10.7, 59.9));
    RAISE NOTICE 'PostGIS: ok';
END $$;

-- Smoke-test Apache AGE (create a temporary graph, then drop it)
DO $$
BEGIN
    LOAD 'age';
    SET search_path = ag_catalog, "$user", public;
    PERFORM create_graph('_augur_smoke_test');
    PERFORM drop_graph('_augur_smoke_test', true);
    RAISE NOTICE 'Apache AGE: ok';
END $$;

-- Record this migration as applied
INSERT INTO schema_migrations (version, description)
VALUES ('000', 'Extension verification and schema baseline')
ON CONFLICT (version) DO NOTHING;
