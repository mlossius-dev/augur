"""
Home-page changes beyond edge weights and condition states
(docs/augur-presentation.md, level 3): new events connected to an active
condition, and sharp market moves, which are data and not graph mutations.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from augur.presentation.changes import get_recent_changes

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)


def _market(symbol: str, label: str, pct: float, cls: str) -> dict:
    return {"symbol": symbol, "label": label, "pct": pct, "value": "1", "class": cls,
            "content_timestamp": NOW}


async def _changes(event_rows: list[dict], market_rows: list[dict]):
    async def fetch(query, *args):
        if "node_type = 'event'" in query:
            return event_rows
        if "pct_change" in query:
            return market_rows
        return []

    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=fetch)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False)))
    with patch("augur.presentation.changes._attach_downstream_counts", AsyncMock()), \
         patch("augur.presentation.changes._attach_topic_membership", AsyncMock()), \
         patch("augur.presentation.changes._attach_sources", AsyncMock()):
        return await get_recent_changes(pool, as_of=NOW, limit=50)


@pytest.mark.asyncio
async def test_market_moves_shown_only_at_or_above_class_threshold() -> None:
    changes = await _changes([], [
        _market("GC=F", "Gold", -3.41, "commodity"),
        _market("BZ=F", "Brent Crude", 2.03, "commodity"),       # below 3 %
        _market("AUDUSD=X", "AUD/USD", 1.75, "currency"),
        _market("EURUSD=X", "EUR/USD", -0.14, "currency"),       # below 1.5 %
        _market("^N225", "Nikkei 225", -4.03, "equity_index"),
        _market("DGS10", "US 10y Treasury Yield", -1.98, "bond_yield"),  # below 5 %
    ])
    names = {c.target_name for c in changes if c.change_type == "market_move"}
    assert names == {"Gold", "AUD/USD", "Nikkei 225"}


@pytest.mark.asyncio
async def test_market_move_is_labelled_and_has_no_graph_target() -> None:
    [c] = await _changes([], [_market("GC=F", "Gold", -3.41, "commodity")])
    assert c.summary == "Market move: Gold down 3.4% over 5 trading days."
    assert c.target_type == "market" and c.target_id == ""
    assert c.weight_before is None and c.weight_after is None


@pytest.mark.asyncio
async def test_new_event_carries_its_node_and_source_signals() -> None:
    node_id, signal = uuid.uuid4(), uuid.uuid4()
    [c] = await _changes([{"node_id": node_id, "name": "US Treasury sanctions on Iran-linked banks",
                           "created_at": NOW, "triggered_by": [signal]}], [])
    assert c.change_type == "event_recorded"
    assert c.summary == "New event: US Treasury sanctions on Iran-linked banks."
    assert (c.target_type, c.target_id) == ("node", str(node_id))
    assert c.signal_ids == [str(signal)]


@pytest.mark.asyncio
async def test_event_without_provenance_has_no_signals() -> None:
    [c] = await _changes([{"node_id": uuid.uuid4(), "name": "Pipeline explosion",
                           "created_at": NOW, "triggered_by": None}], [])
    assert c.signal_ids == []


@pytest.mark.asyncio
async def test_event_query_requires_an_active_condition_and_skips_merged_nodes() -> None:
    seen: list[str] = []

    async def fetch(query, *args):
        seen.append(query)
        return []

    conn = AsyncMock()
    conn.fetch = AsyncMock(side_effect=fetch)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=conn), __aexit__=AsyncMock(return_value=False)))
    with patch("augur.presentation.changes._attach_downstream_counts", AsyncMock()), \
         patch("augur.presentation.changes._attach_topic_membership", AsyncMock()), \
         patch("augur.presentation.changes._attach_sources", AsyncMock()):
        await get_recent_changes(pool, as_of=NOW)
    [q] = [q for q in seen if "node_type = 'event'" in q]
    assert "merged_into IS NULL" in q
    assert "current_state' = 'active'" in q
    assert "NOT e.deprecated" in q
