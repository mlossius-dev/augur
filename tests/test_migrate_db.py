"""
Migrations are recorded as they are applied (APPLIER_TEST_DATABASE_URL).

Before the shared runner, only 000 and 001 recorded themselves, so every
later migration re-ran on each start.
"""

from __future__ import annotations

import os

import pytest

DSN = os.environ.get("APPLIER_TEST_DATABASE_URL", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not DSN, reason="APPLIER_TEST_DATABASE_URL not set"),
]


@pytest.mark.asyncio
async def test_every_migration_is_recorded_once_and_not_rerun() -> None:
    import asyncpg

    from augur.db.migrate import MIGRATIONS_DIR, apply_pending_migrations

    conn = await asyncpg.connect(
        DSN.replace("postgresql+asyncpg://", "postgresql://"),
        server_settings={"search_path": 'ag_catalog, "$user", public'},
    )
    try:
        await apply_pending_migrations(conn)
        recorded = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        on_disk = {p.stem.split("_")[0] for p in MIGRATIONS_DIR.glob("*.sql")}
        assert on_disk <= recorded
        assert await apply_pending_migrations(conn) == []
    finally:
        await conn.close()
