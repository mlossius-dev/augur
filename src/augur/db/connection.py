"""
Async PostgreSQL connection pool management.

One pool per application process. The pool is initialised during application
startup and closed on shutdown. All database-touching code calls get_pool()
to obtain a connection.

Apache AGE requires its search_path to be set per-connection; the
_configure_connection callback handles this automatically for every
connection in the pool.
"""

from __future__ import annotations

import asyncpg
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from augur.config import Settings, get_settings

log = structlog.get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_raw_pool: asyncpg.Pool | None = None


async def _configure_connection(connection: asyncpg.Connection) -> None:
    """
    Per-connection setup called by asyncpg whenever a connection is acquired.

    AGE requires LOAD 'age' and the ag_catalog search_path on every session;
    without this, Cypher queries fail with 'function ag_catalog.* does not exist'.
    """
    await connection.execute("LOAD 'age'")
    await connection.execute("SET search_path = ag_catalog, \"$user\", public")


async def init_db(settings: Settings | None = None) -> None:
    """
    Initialise the asyncpg connection pool and the SQLAlchemy engine.

    Called once during application startup. Safe to call multiple times; the
    second call is a no-op if the pool is already open.
    """
    global _engine, _session_factory, _raw_pool

    if _engine is not None:
        return

    cfg = settings or get_settings()

    log.info("db.init", url=cfg.database_url.split("@")[-1])  # host/db only; no creds

    _engine = create_async_engine(
        cfg.database_url,
        pool_size=cfg.db_pool_min_size,
        max_overflow=cfg.db_pool_max_size - cfg.db_pool_min_size,
        echo=False,
        connect_args={
            "server_settings": {
                "search_path": "ag_catalog, \"$user\", public",
                "application_name": "augur",
            }
        },
    )

    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )

    # Raw asyncpg pool for Cypher queries that bypass SQLAlchemy
    _raw_pool = await asyncpg.create_pool(
        dsn=cfg.database_url.replace("postgresql+asyncpg://", "postgresql://"),
        min_size=cfg.db_pool_min_size,
        max_size=cfg.db_pool_max_size,
        init=_configure_connection,
        command_timeout=30,
    )

    log.info("db.ready")


async def close_db() -> None:
    """Tear down both pools cleanly. Called on application shutdown."""
    global _engine, _session_factory, _raw_pool

    if _raw_pool is not None:
        await _raw_pool.close()
        _raw_pool = None

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None

    log.info("db.closed")


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _session_factory


def get_raw_pool() -> asyncpg.Pool:
    """Return the raw asyncpg pool for Cypher (AGE) queries."""
    if _raw_pool is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _raw_pool
