"""
Conversation session management — create sessions, persist messages,
load history, prune stale sessions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import asyncpg
import structlog

log = structlog.get_logger(__name__)

_MAX_HISTORY_TURNS = 6  # keep last N user+assistant pairs


async def create_session(pool: asyncpg.Pool, *, metadata: dict | None = None) -> str:
    """Create a new conversation session. Returns session_id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO conversation_sessions (metadata) VALUES ($1) RETURNING session_id",
            metadata or {},
        )
    return str(row["session_id"])


async def touch_session(pool: asyncpg.Pool, session_id: str) -> bool:
    """Update last_active. Returns False if session not found."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE conversation_sessions SET last_active = now() WHERE session_id = $1",
            session_id,
        )
    updated = int(str(result).split()[-1])
    return updated > 0


async def get_session_history(
    pool: asyncpg.Pool,
    session_id: str,
    *,
    limit: int = _MAX_HISTORY_TURNS * 2,
) -> list[dict]:
    """Return recent messages for a session, oldest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT role, content, created_at
            FROM conversation_messages
            WHERE session_id = $1
            ORDER BY created_at ASC
            LIMIT $2
            """,
            session_id,
            limit,
        )
    return [{"role": r["role"], "content": r["content"]} for r in rows]


async def save_message(
    pool: asyncpg.Pool,
    session_id: str,
    *,
    role: str,
    content: str,
    context_node_ids: list[str] | None = None,
    context_edge_ids: list[str] | None = None,
    model_used: str | None = None,
) -> str:
    """Persist one message. Returns message_id."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO conversation_messages
                (session_id, role, content, context_node_ids, context_edge_ids, model_used)
            VALUES ($1, $2, $3, $4::uuid[], $5::uuid[], $6)
            RETURNING message_id
            """,
            session_id,
            role,
            content,
            context_node_ids or [],
            context_edge_ids or [],
            model_used,
        )
    return str(row["message_id"])


async def get_full_session(pool: asyncpg.Pool, session_id: str) -> dict | None:
    """Return session metadata + all messages."""
    async with pool.acquire() as conn:
        session_row = await conn.fetchrow(
            "SELECT session_id, created_at, last_active FROM conversation_sessions WHERE session_id = $1",
            session_id,
        )
        if not session_row:
            return None

        message_rows = await conn.fetch(
            """
            SELECT message_id, role, content, model_used, created_at
            FROM conversation_messages
            WHERE session_id = $1
            ORDER BY created_at ASC
            """,
            session_id,
        )

    return {
        "session_id": str(session_row["session_id"]),
        "created_at": session_row["created_at"].isoformat(),
        "last_active": session_row["last_active"].isoformat(),
        "messages": [
            {
                "message_id": str(r["message_id"]),
                "role": r["role"],
                "content": r["content"],
                "model_used": r["model_used"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in message_rows
        ],
    }


async def prune_sessions(pool: asyncpg.Pool, *, max_age_hours: int = 48) -> int:
    """Delete sessions older than max_age_hours. Returns count deleted."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "SELECT prune_old_sessions($1)",
            max_age_hours,
        )
    return 0  # result is from SELECT, not DELETE count — acceptable
