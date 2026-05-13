"""
Augur FastAPI application entry point.

Startup sequence:
  1. Configure structured logging.
  2. Initialise Postgres connection pool.
  3. Run pending database migrations.
  4. Instantiate the LLM client and store it on app.state.
  5. Mount API routers.

The in-process APScheduler is configured here with no jobs in Phase 0;
its slot is reserved for Phase 2+ ingestion and Phase 5 disconfirmation.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from augur.api.health import router as health_router
from augur.config import get_settings
from augur.db.connection import close_db, init_db
from augur.llm.client import LLMClient
from augur.logging import configure_logging

log = structlog.get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(
        log_level=settings.log_level,
        log_format=settings.log_format,
    )

    app = FastAPI(
        title="Augur",
        description=(
            "A reasoning prosthetic for understanding the present "
            "and exploring plausible futures."
        ),
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    @app.on_event("startup")
    async def startup() -> None:
        log.info("augur.startup", env=settings.augur_env)

        await init_db(settings)
        await _run_migrations()

        # Store the LLM client on app.state so health endpoints can reach it
        # via request.app.state.llm_client (see api/health.py _get_llm_client).
        app.state.llm_client = LLMClient.from_settings(settings)

        log.info("augur.ready")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        log.info("augur.shutdown")
        await close_db()

    # ── Routers ───────────────────────────────────────────────────────────────

    app.include_router(health_router)

    return app


async def _run_migrations() -> None:
    """
    Apply any pending database migrations in ascending version order.

    Phase 0 ships one migration (000_init.sql) that verifies extensions and
    creates the schema_migrations table.  Future phases add to this directory.
    """
    from pathlib import Path

    from augur.db.connection import get_raw_pool

    migrations_dir = Path(__file__).parent / "db" / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))

    pool = get_raw_pool()
    async with pool.acquire() as conn:
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

        for migration_file in migration_files:
            version = migration_file.stem.split("_")[0]
            already_applied = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version = $1)",
                version,
            )
            if already_applied:
                continue
            sql = migration_file.read_text()
            log.info("migration.applying", version=version, file=migration_file.name)
            await conn.execute(sql)
            log.info("migration.applied", version=version)


app = create_app()
