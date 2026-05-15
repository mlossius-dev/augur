"""
Conversation orchestrator: retrieve context → load history → call LLM → persist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg
import structlog

from augur.conversation.context import ConversationContext, retrieve_context
from augur.conversation.prompts import SYSTEM_PROMPT, build_messages
from augur.conversation.session import (
    create_session,
    get_session_history,
    save_message,
    touch_session,
)
from augur.llm.client import LLMClient
from augur.llm.models import PipelineStage

log = structlog.get_logger(__name__)


@dataclass
class ConversationTurn:
    session_id: str
    question: str
    answer: str
    context: ConversationContext
    model_used: str
    message_id: str


class ConversationOrchestrator:
    def __init__(self, pool: asyncpg.Pool, llm_client: LLMClient) -> None:
        self._pool = pool
        self._llm = llm_client

    async def ask(
        self,
        question: str,
        *,
        session_id: str | None = None,
        as_of: datetime | None = None,
    ) -> ConversationTurn:
        """
        Answer one question grounded in graph evidence.

        If session_id is None, creates a new session.
        If session_id is provided, loads prior history for multi-turn context.
        """
        # Ensure session exists
        if session_id is None:
            session_id = await create_session(self._pool)
        else:
            exists = await touch_session(self._pool, session_id)
            if not exists:
                session_id = await create_session(self._pool)

        # Retrieve graph context
        ctx = await retrieve_context(self._pool, question, as_of=as_of)

        # Load conversation history (prior turns)
        history = await get_session_history(self._pool, session_id)

        # Build LLM messages
        messages = build_messages(ctx, history)

        # Persist the user message
        await save_message(
            self._pool,
            session_id,
            role="user",
            content=question,
            context_node_ids=[n["node_id"] for n in ctx.matched_nodes],
            context_edge_ids=[e["edge_id"] for e in ctx.connected_edges],
        )

        # Call LLM
        response = await self._llm.complete(
            stage=PipelineStage.CONVERSATION,
            prompt_template_id="conversation_v1",
            messages=messages,
            system=SYSTEM_PROMPT,
            metadata={
                "session_id": session_id,
                "n_nodes": len(ctx.matched_nodes),
                "n_edges": len(ctx.connected_edges),
                "n_signals": len(ctx.recent_signals),
            },
        )

        answer = response.content.strip()

        # Persist the assistant reply
        message_id = await save_message(
            self._pool,
            session_id,
            role="assistant",
            content=answer,
            model_used=response.model,
        )

        log.info(
            "conversation.turn_complete",
            session_id=session_id,
            n_nodes=len(ctx.matched_nodes),
            model=response.model,
        )

        return ConversationTurn(
            session_id=session_id,
            question=question,
            answer=answer,
            context=ctx,
            model_used=response.model,
            message_id=message_id,
        )
