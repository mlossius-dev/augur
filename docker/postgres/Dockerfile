# Postgres 16 with all required Augur extensions:
#   pgvector   — claim-level embeddings for Tier A deduplication
#   pg_trgm    — fuzzy text matching for entity resolution
#   PostGIS    — geospatial indexing for location-tagged signals
#   Apache AGE — Cypher-compatible graph layer over Postgres
#
# pg_trgm is bundled with Postgres; the others are installed here.
FROM postgres:16-bookworm

# Build tools and PostGIS from apt
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        git \
        postgresql-server-dev-16 \
        flex \
        bison \
        postgresql-16-postgis-3 \
        postgresql-16-postgis-3-scripts \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# pgvector — semantic similarity search over claim vectors
ARG PGVECTOR_VERSION=v0.8.0
RUN git clone --branch ${PGVECTOR_VERSION} --depth 1 \
        https://github.com/pgvector/pgvector.git /tmp/pgvector \
    && cd /tmp/pgvector \
    && make \
    && make install \
    && rm -rf /tmp/pgvector

# Apache AGE — Cypher graph queries over Postgres tables
ARG AGE_VERSION=PG16/v1.5.0
RUN git clone --branch ${AGE_VERSION} --depth 1 \
        https://github.com/apache/age.git /tmp/age \
    && cd /tmp/age \
    && make \
    && make install \
    && rm -rf /tmp/age

# Initialisation SQL runs automatically at first start
COPY init.sql /docker-entrypoint-initdb.d/000_extensions.sql
