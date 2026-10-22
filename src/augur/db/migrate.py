"""
Database migrations: apply the pending files in db/migrations, in order.

Shared by `augur migrate` and app startup.  Each migration runs in its own
transaction together with its row in schema_migrations, so a migration is
either applied and recorded or neither.  (Before this, only 000 and 001
recorded themselves, and every later file re-ran on each start.)
"""

from __future__ import annotations

from pathlib import Path

import asyncpg
import structlog

log = structlog.get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def _description(path: Path) -> str:
    """The file's first comment line, else its name."""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("--") and line.strip("- "):
            return line.lstrip("- ").strip()
    return path.stem


async def apply_pending_migrations(conn: asyncpg.Connection) -> list[str]:
    """Apply every migration not yet in schema_migrations; return their file names."""
    # schema_migrations may not exist yet on a fresh database
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            id          SERIAL      PRIMARY KEY,
            version     TEXT        NOT NULL UNIQUE,
            description TEXT        NOT NULL,
            applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    applied: list[str] = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem.split("_")[0]
        if await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version = $1)", version
        ):
            continue
        log.info("migration.applying", version=version, file=path.name)
        async with conn.transaction():
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO schema_migrations (version, description) VALUES ($1, $2) "
                "ON CONFLICT (version) DO NOTHING",
                version,
                _description(path),
            )
        log.info("migration.applied", version=version)
        applied.append(path.name)
    return applied
