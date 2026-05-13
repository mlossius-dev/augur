-- Bootstrap all required extensions in the augur database.
-- This file is executed once on first container startup.

-- Semantic similarity search for claim-level deduplication (Tier A)
CREATE EXTENSION IF NOT EXISTS vector;

-- Fuzzy text matching for entity alias resolution during anchoring
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Geospatial indexing for location-tagged signals (shipping, seismic, mining)
CREATE EXTENSION IF NOT EXISTS postgis;

-- Cypher-compatible graph layer for Tier B graph traversal
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- Verify all extensions loaded correctly
DO $$
DECLARE
    ext TEXT;
    missing TEXT[] := '{}';
BEGIN
    FOREACH ext IN ARRAY ARRAY['vector', 'pg_trgm', 'postgis', 'age'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = ext) THEN
            missing := missing || ext;
        END IF;
    END LOOP;
    IF array_length(missing, 1) > 0 THEN
        RAISE EXCEPTION 'Missing extensions: %', array_to_string(missing, ', ');
    END IF;
    RAISE NOTICE 'All Augur extensions loaded: vector, pg_trgm, postgis, age';
END $$;
