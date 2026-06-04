"""
Conversation API — natural language queries grounded in graph evidence.

POST /api/conversation/query          → ask a question (creates or continues session)
GET  /api/conversation/{session_id}   → retrieve full session history
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.requests import Request
from pydantic import BaseModel, Field

from augur.conversation.orchestrator import ConversationOrchestrator
from augur.conversation.session import get_full_session

router = APIRouter(prefix="/api/conversation", tags=["conversation"])


def _pool(request: Request):
    return request.app.state.raw_pool


def _llm(request: Request):
    return request.app.state.llm_client


class QueryRequest(BaseModel):
    question: Annotated[str, Field(min_length=1, max_length=2000)]
    session_id: str | None = None
    as_of: str | None = None


def _parse_as_of(as_of: str | None) -> datetime | None:
    if not as_of:
        return None
    try:
        dt = datetime.fromisoformat(as_of)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid as_of timestamp")


@router.post("/query")
async def query(
    body: QueryRequest,
    pool=Depends(_pool),
    llm=Depends(_llm),
):
    """
    Ask a question about the world state.

    Retrieves relevant graph evidence, calls the LLM, and returns a grounded
    answer.  Pass session_id to continue a prior conversation.
    """
    as_of = _parse_as_of(body.as_of)
    orch = ConversationOrchestrator(pool, llm)

    try:
        turn = await orch.ask(
            body.question,
            session_id=body.session_id,
            as_of=as_of,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Conversation failed: {exc}")

    return {
        "session_id": turn.session_id,
        "question": turn.question,
        "answer": turn.answer,
        "model_used": turn.model_used,
        "message_id": turn.message_id,
        "context": {
            "n_nodes": len(turn.context.matched_nodes),
            "n_edges": len(turn.context.connected_edges),
            "n_signals": len(turn.context.recent_signals),
            "matched_node_names": [n["name"] for n in turn.context.matched_nodes[:5]],
        },
    }


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    pool=Depends(_pool),
):
    """Return full conversation history for a session."""
    session = await get_full_session(pool, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session
