"""
Phase 11 tests — conversation layer.

Covers:
  - conversation/context.py     — ConversationContext, retrieve_context
  - conversation/prompts.py     — build_context_block, build_messages
  - conversation/session.py     — create/touch/save/load session
  - conversation/orchestrator.py — ConversationOrchestrator (LLM mocked)
  - api/conversation.py         — POST /query, GET /{session_id}
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_pool(*fetch_results, fetchrow_result=None, execute_result="UPDATE 1"):
    pool = MagicMock()
    conn = AsyncMock()

    call_count = {"n": 0}

    async def fetch_side_effect(query, *args):
        idx = call_count["n"]
        call_count["n"] += 1
        return fetch_results[idx] if idx < len(fetch_results) else []

    conn.fetch.side_effect = fetch_side_effect
    conn.fetchrow.return_value = fetchrow_result
    conn.execute.return_value = execute_result
    conn.executemany.return_value = execute_result

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool, conn


def _make_conv_client(mock_pool=None, mock_llm=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import augur.api.conversation as conv_mod
    from augur.api.conversation import router

    app = FastAPI()
    app.include_router(router)
    pool = mock_pool or MagicMock()
    llm = mock_llm or MagicMock()
    app.dependency_overrides[conv_mod._pool] = lambda: pool
    app.dependency_overrides[conv_mod._llm] = lambda: llm
    return TestClient(app), pool, llm


# ── ConversationContext ───────────────────────────────────────────────────────


class TestConversationContext:
    def test_dataclass_instantiation(self):
        from augur.conversation.context import ConversationContext

        ctx = ConversationContext(
            question="What is driving inflation?",
            matched_nodes=[{"node_id": "a", "name": "CPI", "node_type": "quantity",
                            "current_state": None, "description": ""}],
            connected_edges=[],
            recent_signals=[],
            dimension_summary=[],
        )
        assert ctx.question == "What is driving inflation?"
        assert len(ctx.matched_nodes) == 1

    def test_default_dimension_summary(self):
        from augur.conversation.context import ConversationContext

        ctx = ConversationContext(
            question="Q", matched_nodes=[], connected_edges=[], recent_signals=[]
        )
        assert ctx.dimension_summary == []


class TestRetrieveContext:
    @pytest.mark.asyncio
    async def test_returns_context_with_empty_graph(self):
        from augur.conversation.context import retrieve_context

        pool, _ = _make_pool([], [], [], [])
        ctx = await retrieve_context(pool, "What is happening with oil prices?")
        assert ctx.question == "What is happening with oil prices?"
        assert ctx.matched_nodes == []
        assert ctx.connected_edges == []

    @pytest.mark.asyncio
    async def test_maps_node_rows_to_dicts(self):
        from augur.conversation.context import retrieve_context

        nid = uuid.uuid4()
        nodes = [
            {
                "node_id": nid, "name": "Oil price spike", "node_type": "condition",
                "description": "Rapid increase in crude prices", "current_state": "active",
                "score": 0.8,
            }
        ]
        pool, _ = _make_pool(nodes, [], [], [])
        ctx = await retrieve_context(pool, "oil price")
        assert len(ctx.matched_nodes) == 1
        assert ctx.matched_nodes[0]["name"] == "Oil price spike"
        assert ctx.matched_nodes[0]["current_state"] == "active"

    @pytest.mark.asyncio
    async def test_skips_edge_fetch_when_no_nodes(self):
        from augur.conversation.context import retrieve_context

        pool, conn = _make_pool([], [])
        await retrieve_context(pool, "question with no matches")
        # Should only have called fetch twice: nodes + dimension summary
        assert conn.fetch.call_count <= 3  # nodes, maybe signals fallback, dim summary

    @pytest.mark.asyncio
    async def test_maps_edge_rows_correctly(self):
        from augur.conversation.context import retrieve_context

        nid = uuid.uuid4()
        eid = uuid.uuid4()
        nodes = [
            {"node_id": nid, "name": "Gas storage", "node_type": "quantity",
             "description": "", "current_state": None, "score": 0.6}
        ]
        edges = [
            {"edge_id": eid, "edge_type": "causes", "current_weight_band": "strong",
             "reasoning": "Low storage causes price rises",
             "source_name": "Gas storage", "target_name": "Energy price"}
        ]
        pool, _ = _make_pool(nodes, edges, [], [])
        ctx = await retrieve_context(pool, "gas storage")
        assert len(ctx.connected_edges) == 1
        assert ctx.connected_edges[0]["weight_band"] == "strong"


# ── Prompts ───────────────────────────────────────────────────────────────────


class TestBuildContextBlock:
    def _ctx(self, nodes=None, edges=None, signals=None):
        from augur.conversation.context import ConversationContext

        return ConversationContext(
            question="Q",
            matched_nodes=nodes or [],
            connected_edges=edges or [],
            recent_signals=signals or [],
        )

    def test_empty_evidence_shows_none_placeholder(self):
        from augur.conversation.prompts import build_context_block

        block = build_context_block(self._ctx())
        assert "(no matching nodes found)" in block

    def test_includes_node_names(self):
        from augur.conversation.prompts import build_context_block

        ctx = self._ctx(nodes=[{
            "node_id": "abc", "name": "Gas storage levels",
            "node_type": "quantity", "current_state": None, "description": ""
        }])
        block = build_context_block(ctx)
        assert "Gas storage levels" in block

    def test_active_conditions_flagged(self):
        from augur.conversation.prompts import build_context_block

        ctx = self._ctx(nodes=[{
            "node_id": "abc", "name": "Credit crunch",
            "node_type": "condition", "current_state": "active", "description": ""
        }])
        block = build_context_block(ctx)
        assert "[ACTIVE]" in block

    def test_includes_edge_weight(self):
        from augur.conversation.prompts import build_context_block

        ctx = self._ctx(edges=[{
            "edge_id": "e1", "source_name": "A", "edge_type": "causes",
            "target_name": "B", "weight_band": "strong", "reasoning": ""
        }])
        block = build_context_block(ctx)
        assert "strong" in block
        assert "causes" in block

    def test_includes_signal_claim(self):
        from augur.conversation.prompts import build_context_block

        ctx = self._ctx(signals=[{
            "claim_text": "OPEC cut output by 1mb/d",
            "lens_id": "commodities",
            "confidence_band": "high",
            "content_timestamp": "2024-06-01T00:00:00Z",
        }])
        block = build_context_block(ctx)
        assert "OPEC cut output" in block


class TestBuildMessages:
    def _ctx(self):
        from augur.conversation.context import ConversationContext

        return ConversationContext(
            question="What is driving inflation?",
            matched_nodes=[],
            connected_edges=[],
            recent_signals=[],
        )

    def test_no_history_returns_single_user_message(self):
        from augur.conversation.prompts import build_messages

        msgs = build_messages(self._ctx(), history=[])
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert "What is driving inflation?" in msgs[0]["content"]

    def test_with_history_appends_question(self):
        from augur.conversation.prompts import build_messages

        history = [
            {"role": "user", "content": "Prior question"},
            {"role": "assistant", "content": "Prior answer"},
        ]
        msgs = build_messages(self._ctx(), history=history)
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "What is driving inflation?"

    def test_context_block_always_present(self):
        from augur.conversation.prompts import build_messages

        msgs = build_messages(self._ctx(), history=[])
        full = " ".join(m["content"] for m in msgs)
        assert "Graph evidence" in full


# ── Session management ────────────────────────────────────────────────────────


class TestCreateSession:
    @pytest.mark.asyncio
    async def test_returns_session_id_string(self):
        from augur.conversation.session import create_session

        sid = uuid.uuid4()
        pool, conn = _make_pool()
        conn.fetchrow.return_value = {"session_id": sid}

        result = await create_session(pool)
        assert result == str(sid)

    @pytest.mark.asyncio
    async def test_accepts_metadata(self):
        from augur.conversation.session import create_session

        sid = uuid.uuid4()
        pool, conn = _make_pool()
        conn.fetchrow.return_value = {"session_id": sid}

        result = await create_session(pool, metadata={"source": "cli"})
        assert result == str(sid)


class TestTouchSession:
    @pytest.mark.asyncio
    async def test_returns_true_when_updated(self):
        from augur.conversation.session import touch_session

        pool, conn = _make_pool()
        conn.execute.return_value = "UPDATE 1"
        result = await touch_session(pool, str(uuid.uuid4()))
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self):
        from augur.conversation.session import touch_session

        pool, conn = _make_pool()
        conn.execute.return_value = "UPDATE 0"
        result = await touch_session(pool, str(uuid.uuid4()))
        assert result is False


class TestSaveMessage:
    @pytest.mark.asyncio
    async def test_returns_message_id(self):
        from augur.conversation.session import save_message

        mid = uuid.uuid4()
        pool, conn = _make_pool()
        conn.fetchrow.return_value = {"message_id": mid}

        result = await save_message(
            pool, str(uuid.uuid4()),
            role="user", content="test question",
        )
        assert result == str(mid)

    @pytest.mark.asyncio
    async def test_accepts_context_ids(self):
        from augur.conversation.session import save_message

        mid = uuid.uuid4()
        pool, conn = _make_pool()
        conn.fetchrow.return_value = {"message_id": mid}

        result = await save_message(
            pool, str(uuid.uuid4()),
            role="assistant", content="answer",
            context_node_ids=[str(uuid.uuid4())],
            context_edge_ids=[str(uuid.uuid4())],
            model_used="gemini-flash",
        )
        assert result == str(mid)


class TestGetSessionHistory:
    @pytest.mark.asyncio
    async def test_returns_role_content_pairs(self):
        from augur.conversation.session import get_session_history

        rows = [
            {"role": "user", "content": "Q1", "created_at": datetime.now(timezone.utc)},
            {"role": "assistant", "content": "A1", "created_at": datetime.now(timezone.utc)},
        ]
        pool, _ = _make_pool(rows)
        history = await get_session_history(pool, str(uuid.uuid4()))
        assert len(history) == 2
        assert history[0] == {"role": "user", "content": "Q1"}

    @pytest.mark.asyncio
    async def test_returns_empty_for_unknown_session(self):
        from augur.conversation.session import get_session_history

        pool, _ = _make_pool([])
        history = await get_session_history(pool, str(uuid.uuid4()))
        assert history == []


class TestGetFullSession:
    @pytest.mark.asyncio
    async def test_returns_none_when_not_found(self):
        from augur.conversation.session import get_full_session

        pool, conn = _make_pool([])
        conn.fetchrow.return_value = None
        result = await get_full_session(pool, str(uuid.uuid4()))
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_session_with_messages(self):
        from augur.conversation.session import get_full_session

        sid = uuid.uuid4()
        mid = uuid.uuid4()
        now = datetime.now(timezone.utc)

        session_row = {"session_id": sid, "created_at": now, "last_active": now}
        message_rows = [
            {"message_id": mid, "role": "user", "content": "Q",
             "model_used": None, "created_at": now}
        ]
        pool, conn = _make_pool(message_rows)
        conn.fetchrow.return_value = session_row

        result = await get_full_session(pool, str(sid))
        assert result is not None
        assert result["session_id"] == str(sid)
        assert len(result["messages"]) == 1
        assert result["messages"][0]["role"] == "user"


# ── Orchestrator ──────────────────────────────────────────────────────────────


class TestConversationOrchestrator:
    def _make_orch(self, answer="The graph shows X."):
        from augur.conversation.orchestrator import ConversationOrchestrator

        pool, conn = _make_pool([], [], [], [])

        # session create / touch
        conn.fetchrow.return_value = {"session_id": uuid.uuid4(),
                                      "message_id": uuid.uuid4()}
        conn.execute.return_value = "UPDATE 1"

        llm = MagicMock()
        response = MagicMock()
        response.content = answer
        response.model = "gemini-flash"
        llm.complete = AsyncMock(return_value=response)

        orch = ConversationOrchestrator(pool, llm)
        return orch, pool, conn

    @pytest.mark.asyncio
    async def test_returns_conversation_turn(self):
        from augur.conversation.orchestrator import ConversationTurn

        orch, _, _ = self._make_orch("The graph does not have sufficient evidence.")
        turn = await orch.ask("What is driving food prices?")
        assert isinstance(turn, ConversationTurn)
        assert turn.answer == "The graph does not have sufficient evidence."
        assert turn.session_id is not None

    @pytest.mark.asyncio
    async def test_creates_new_session_when_none(self):
        orch, _, conn = self._make_orch()
        turn = await orch.ask("Test question")
        assert turn.session_id is not None

    @pytest.mark.asyncio
    async def test_calls_llm_once(self):
        orch, _, _ = self._make_orch()
        await orch.ask("Test")
        assert orch._llm.complete.call_count == 1


# ── API ───────────────────────────────────────────────────────────────────────


class TestConversationApiQuery:
    def test_requires_question(self):
        client, _, _ = _make_conv_client()
        resp = client.post("/api/conversation/query", json={})
        assert resp.status_code == 422

    def test_empty_question_rejected(self):
        client, _, _ = _make_conv_client()
        resp = client.post("/api/conversation/query", json={"question": ""})
        assert resp.status_code == 422

    def test_returns_200_with_answer(self):
        from augur.conversation.orchestrator import ConversationTurn
        from augur.conversation.context import ConversationContext

        ctx = ConversationContext(question="Q", matched_nodes=[], connected_edges=[], recent_signals=[])
        turn = ConversationTurn(
            session_id=str(uuid.uuid4()),
            question="Q",
            answer="A grounded answer.",
            context=ctx,
            model_used="gemini-flash",
            message_id=str(uuid.uuid4()),
        )

        client, _, _ = _make_conv_client()
        with patch("augur.api.conversation.ConversationOrchestrator") as MockOrch:
            mock_instance = MagicMock()
            mock_instance.ask = AsyncMock(return_value=turn)
            MockOrch.return_value = mock_instance
            resp = client.post("/api/conversation/query", json={"question": "Q"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "A grounded answer."
        assert "session_id" in data
        assert "context" in data

    def test_rejects_bad_as_of(self):
        client, _, _ = _make_conv_client()
        resp = client.post(
            "/api/conversation/query",
            json={"question": "Q", "as_of": "not-a-date"},
        )
        assert resp.status_code == 422


class TestConversationApiGetSession:
    def test_returns_404_for_unknown(self):
        client, _, _ = _make_conv_client()
        with patch("augur.api.conversation.get_full_session", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = None
            resp = client.get(f"/api/conversation/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_returns_session_data(self):
        now = datetime.now(timezone.utc).isoformat()
        session_data = {
            "session_id": str(uuid.uuid4()),
            "created_at": now,
            "last_active": now,
            "messages": [],
        }
        client, _, _ = _make_conv_client()
        with patch("augur.api.conversation.get_full_session", new_callable=AsyncMock) as mock_fn:
            mock_fn.return_value = session_data
            resp = client.get(f"/api/conversation/{session_data['session_id']}")
        assert resp.status_code == 200
        assert resp.json()["messages"] == []


# ── Router registration ───────────────────────────────────────────────────────


class TestRouterRegistration:
    def test_conversation_router_importable(self):
        from augur.api.conversation import router
        assert router is not None

    def test_router_has_expected_routes(self):
        from augur.api.conversation import router

        paths = {r.path for r in router.routes}
        assert "/api/conversation/query" in paths
        assert "/api/conversation/{session_id}" in paths
