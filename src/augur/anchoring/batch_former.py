"""
Batch former for the anchoring stage.

Responsibility: pull unanchored signals from Tier A and group them into
topically-coherent batches ready for a single LLM anchoring call.

Topical grouping strategy (Phase 3):
  1. Extract the set of proposed entity names from each signal's
     proposed_anchors list.
  2. Two signals are topically adjacent if their entity name sets share at
     least one member (case-insensitive).
  3. Groups are formed greedily using a union-find over signal indices.
  4. Each resulting group is capped at MAX_BATCH_SIZE signals; larger groups
     are split into contiguous windows.

Phase 3 keeps grouping simple and hash-based.  Phase 5+ will use pgvector
cosine similarity to merge signals that talk about the *same* entity under
different names.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


MAX_BATCH_SIZE = 20  # Maximum signals in a single anchoring LLM call

# Minimum number of signals required to form a batch.  Batches smaller than
# this are held back to be absorbed by a larger future batch, unless
# force=True is passed (used by the `augur anchor` CLI command).
MIN_BATCH_SIZE = 2


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class AnchorBatch:
    """A group of topically-related signals ready for the anchoring LLM."""

    batch_id: UUID = field(default_factory=uuid.uuid4)
    signals: list[dict[str, Any]] = field(default_factory=list)
    # Lens IDs present in this batch; usually a singleton but can be mixed.
    lens_ids: frozenset[str] = field(default_factory=frozenset)
    formed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def signal_ids(self) -> list[UUID]:
        return [s["signal_id"] for s in self.signals]

    @property
    def content_timestamp(self) -> datetime:
        """
        Representative content_timestamp for this batch.

        Uses the oldest content_timestamp in the batch so the anchoring
        applier's replay-mode anchor is conservative (doesn't backdate edges
        to a time after some signals were observed).
        """
        timestamps = [
            s["content_timestamp"]
            for s in self.signals
            if s.get("content_timestamp") is not None
        ]
        if not timestamps:
            return self.formed_at
        return min(timestamps)


# ── Public API ────────────────────────────────────────────────────────────────


def form_batches(
    signals: list[dict[str, Any]],
    *,
    force: bool = False,
) -> list[AnchorBatch]:
    """
    Group a flat list of signals into topically-coherent AnchorBatch objects.

    Args:
        signals: Unanchored signals from TierAStore.get_unanchored().
        force: If True, include batches smaller than MIN_BATCH_SIZE.
               Used when the operator manually triggers anchoring.

    Returns:
        A list of AnchorBatch objects, each containing at most MAX_BATCH_SIZE
        signals.  Signals with no proposed_anchors form their own singleton
        batches (dropped unless force=True).
    """
    if not signals:
        return []

    # Build entity name sets per signal
    entity_sets: list[frozenset[str]] = [
        _extract_entity_names(sig) for sig in signals
    ]

    # Union-find grouping
    parent = list(range(len(signals)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    # Two signals are adjacent if they share at least one entity name
    for i in range(len(signals)):
        if not entity_sets[i]:
            continue
        for j in range(i + 1, len(signals)):
            if entity_sets[i] & entity_sets[j]:
                union(i, j)

    # Collect groups
    groups: dict[int, list[int]] = {}
    for idx in range(len(signals)):
        root = find(idx)
        groups.setdefault(root, []).append(idx)

    batches: list[AnchorBatch] = []
    for indices in groups.values():
        # Split oversized groups into windows
        for window_start in range(0, len(indices), MAX_BATCH_SIZE):
            window = indices[window_start : window_start + MAX_BATCH_SIZE]
            batch_signals = [signals[i] for i in window]

            if len(batch_signals) < MIN_BATCH_SIZE and not force:
                continue

            lens_ids = frozenset(
                s["lens_id"] for s in batch_signals if s.get("lens_id")
            )
            batches.append(
                AnchorBatch(signals=batch_signals, lens_ids=lens_ids)
            )

    return batches


# ── Helpers ───────────────────────────────────────────────────────────────────


def _extract_entity_names(signal: dict[str, Any]) -> frozenset[str]:
    """
    Extract lowercase entity names from a signal's proposed_anchors list.

    Only considers create_node operations with node_type == "entity" or any
    create_node operation with a name field — this is the primary topicality
    signal.
    """
    names: set[str] = set()
    for anchor in signal.get("proposed_anchors", []):
        if not isinstance(anchor, dict):
            continue
        if anchor.get("operation") != "create_node":
            continue
        fields = anchor.get("fields", {})
        name = fields.get("name", "")
        if name and isinstance(name, str):
            names.add(name.strip().lower())
    return frozenset(names)
