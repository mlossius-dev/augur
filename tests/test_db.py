"""
Database layer tests.

Unit tests validate the connection module's behaviour in isolation.
Integration tests (marked @pytest.mark.integration) require a live Postgres
instance with all four extensions installed.
"""

from __future__ import annotations

import pytest

from tests.conftest import requires_db


# ── Unit tests ────────────────────────────────────────────────────────────────


class TestConnectionInit:
    """Connection pool initialisation and teardown."""

    @pytest.mark.asyncio
    async def test_double_init_is_safe(self, minimal_env: None) -> None:
        """Calling init_db twice should not raise or create a second pool."""
        from augur.config import get_settings
        from augur.db.connection import _engine, close_db, init_db

        # Ensure clean state
        get_settings.cache_clear()
        await close_db()

        await init_db()
        from augur.db.connection import _engine as e1

        await init_db()  # second call
        from augur.db.connection import _engine as e2

        assert e1 is e2, "Second init_db() should reuse the existing engine"
        await close_db()

    @pytest.mark.asyncio
    async def test_close_db_when_not_initialised_is_safe(self) -> None:
        """close_db() on an uninitialised pool should not raise."""
        from augur.db.connection import close_db

        # May or may not be initialised — either way should not raise
        await close_db()
        await close_db()  # idempotent

    def test_get_engine_before_init_raises(self) -> None:
        """Accessing the engine before init_db() should raise RuntimeError."""
        import asyncio

        from augur.db.connection import _engine, close_db, get_engine

        asyncio.run(close_db())  # ensure clean state

        with pytest.raises(RuntimeError, match="not initialised"):
            get_engine()

    def test_get_raw_pool_before_init_raises(self) -> None:
        from augur.db.connection import get_raw_pool

        with pytest.raises(RuntimeError, match="not initialised"):
            get_raw_pool()


# ── Integration tests ─────────────────────────────────────────────────────────


@pytest.mark.integration
@requires_db()
class TestExtensions:
    """Verify all four required Postgres extensions are present and functional."""

    @pytest.fixture(autouse=True)
    async def setup_db(self, minimal_env: None) -> None:
        from augur.db.connection import close_db, init_db

        await init_db()
        yield
        await close_db()

    @pytest.mark.asyncio
    async def test_pgvector_installed(self) -> None:
        from augur.db.connection import get_raw_pool

        pool = get_raw_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT '[1,2,3]'::vector AS v")
        assert row is not None
        assert str(row["v"]) == "[1,2,3]"

    @pytest.mark.asyncio
    async def test_pg_trgm_installed(self) -> None:
        from augur.db.connection import get_raw_pool

        pool = get_raw_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT similarity('augur', 'auger') AS s")
        assert row is not None
        assert 0.0 < float(row["s"]) < 1.0

    @pytest.mark.asyncio
    async def test_postgis_installed(self) -> None:
        from augur.db.connection import get_raw_pool

        pool = get_raw_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT ST_AsText(ST_Point(10.73, 59.91)) AS p"
            )
        assert row is not None
        assert "POINT" in row["p"]

    @pytest.mark.asyncio
    async def test_age_installed(self) -> None:
        """Create and immediately drop a temporary AGE graph."""
        from augur.db.connection import get_raw_pool

        pool = get_raw_pool()
        async with pool.acquire() as conn:
            await conn.execute("LOAD 'age'")
            await conn.execute(
                "SET search_path = ag_catalog, \"$user\", public"
            )
            await conn.execute("SELECT create_graph('_test_graph')")
            await conn.execute("SELECT drop_graph('_test_graph', true)")

    @pytest.mark.asyncio
    async def test_schema_migrations_table_exists(self) -> None:
        """The schema_migrations table should exist after migration 000."""
        from augur.db.connection import get_raw_pool

        pool = get_raw_pool()
        async with pool.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'schema_migrations')"
            )
        assert exists, "schema_migrations table missing — run migrations"
