"""
Lens executor — runs one or more lenses against a single payload.

Each lens call is an LLM completion that produces a list of signal dicts.
Lenses run in parallel for the same payload (no cross-contamination).

The executor validates the LLM output, enforces the max_signals cap,
and returns structured signal dicts ready for Tier A storage.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog

from augur.extraction.lens import LensConfig
from augur.graph.schema import MAX_ANCHORS_PER_SIGNAL, ConfidenceBand

log = structlog.get_logger(__name__)

_VALID_CONFIDENCE_BANDS = {b.value for b in ConfidenceBand}


class LensExecutor:
    """
    Runs extraction lenses against payloads.

    Requires an LLMClient instance for completions.
    """

    def __init__(self, llm_client) -> None:  # type: ignore[type-arg]
        self._llm = llm_client

    async def extract(
        self,
        *,
        payload_id: UUID,
        content: str,
        content_timestamp: datetime,
        source_id: str,
        lens: LensConfig,
    ) -> list[dict[str, Any]]:
        """
        Run `lens` against `content` and return signal dicts.

        Each signal dict has the shape:
            signal_id, payload_id, lens_id, lens_version, claim_text,
            confidence_band, proposed_anchors, reasoning, content_timestamp,
            extracted_at

        Returns [] if the payload is irrelevant to the lens or the LLM fails.
        """
        from augur.llm.models import PipelineStage

        user_message = f"<payload source_id={source_id!r}>\n{content}\n</payload>"

        try:
            response = await self._llm.complete(
                stage=PipelineStage.EXTRACTION,
                prompt_template_id=f"lens_{lens.lens_id}_v{lens.lens_version}",
                messages=[
                    {"role": "system", "content": lens.system_prompt},
                    {"role": "user", "content": user_message},
                ],
                metadata={
                    "lens_id": lens.lens_id,
                    "source_id": source_id,
                    "payload_id": str(payload_id),
                },
            )
        except Exception as exc:
            log.warning(
                "executor.llm_failed",
                lens_id=lens.lens_id,
                payload_id=str(payload_id),
                error=str(exc),
            )
            return []

        raw_signals = _parse_llm_output(response.content, lens_id=lens.lens_id)
        if not raw_signals:
            return []

        # Enforce per-payload signal cap
        raw_signals = raw_signals[: lens.max_signals]

        extracted_at = datetime.now(timezone.utc)
        signals: list[dict[str, Any]] = []

        for raw in raw_signals:
            validated = _validate_signal(raw, lens=lens)
            if validated is None:
                continue

            signals.append(
                {
                    "signal_id": uuid.uuid4(),
                    "payload_id": payload_id,
                    "lens_id": lens.lens_id,
                    "lens_version": lens.lens_version,
                    "claim_text": validated["claim_text"],
                    "confidence_band": validated["confidence_band"],
                    "proposed_anchors": validated["proposed_anchors"],
                    "reasoning": validated.get("reasoning"),
                    "content_timestamp": content_timestamp,
                    "extracted_at": extracted_at,
                }
            )

        log.info(
            "executor.extracted",
            lens_id=lens.lens_id,
            payload_id=str(payload_id),
            n_signals=len(signals),
            model=response.model,
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
        )
        return signals

    async def extract_all_lenses(
        self,
        *,
        payload_id: UUID,
        content: str,
        content_timestamp: datetime,
        source_id: str,
        lenses: list[LensConfig],
    ) -> list[dict[str, Any]]:
        """
        Run all lenses in parallel against the same payload.

        Returns the merged flat list of signals from all lenses.
        Lens failures are logged and skipped.
        """
        tasks = [
            self.extract(
                payload_id=payload_id,
                content=content,
                content_timestamp=content_timestamp,
                source_id=source_id,
                lens=lens,
            )
            for lens in lenses
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        signals: list[dict[str, Any]] = []
        for lens, result in zip(lenses, results):
            if isinstance(result, Exception):
                log.error(
                    "executor.lens_exception",
                    lens_id=lens.lens_id,
                    error=str(result),
                )
            else:
                signals.extend(result)

        return signals


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_llm_output(content: str, *, lens_id: str) -> list[dict[str, Any]]:
    """
    Parse the LLM response text as JSON.

    The model is instructed to return a JSON array; if the response is wrapped
    in a markdown code block we strip it.
    """
    text = content.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.splitlines()
        # drop first line (```json or ```) and last line (```)
        text = "\n".join(lines[1:-1]).strip()

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract a JSON array from the response
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                log.warning("executor.parse_failed", lens_id=lens_id, content_preview=text[:200])
                return []
        else:
            log.warning("executor.parse_failed", lens_id=lens_id, content_preview=text[:200])
            return []

    if not isinstance(parsed, list):
        log.warning("executor.not_a_list", lens_id=lens_id)
        return []

    return parsed


def _validate_signal(
    raw: dict[str, Any], *, lens: LensConfig
) -> dict[str, Any] | None:
    """
    Validate a single raw signal dict.

    Returns the validated dict (possibly sanitized) or None if invalid.
    """
    if not isinstance(raw, dict):
        return None

    claim_text = str(raw.get("claim_text", "")).strip()
    if not claim_text or len(claim_text) > 500:
        return None

    confidence_band = raw.get("confidence_band", "")
    if confidence_band not in _VALID_CONFIDENCE_BANDS:
        confidence_band = "weak_inference"

    proposed_anchors = raw.get("proposed_anchors", [])
    if not isinstance(proposed_anchors, list):
        proposed_anchors = []

    # Enforce per-signal anchor cap
    proposed_anchors = proposed_anchors[:MAX_ANCHORS_PER_SIGNAL]

    # Filter anchors to lens graph_scope
    proposed_anchors = _filter_anchors_to_scope(proposed_anchors, lens)

    return {
        "claim_text": claim_text,
        "confidence_band": confidence_band,
        "proposed_anchors": proposed_anchors,
        "reasoning": str(raw.get("reasoning", ""))[:1000],
    }


def _filter_anchors_to_scope(
    anchors: list[dict[str, Any]], lens: LensConfig
) -> list[dict[str, Any]]:
    """
    Remove anchor operations that propose node/edge types outside the lens's graph_scope.
    """
    allowed_nodes = {n.value for n in lens.graph_scope_nodes}
    allowed_edges = {e.value for e in lens.graph_scope_edges}
    filtered = []

    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        op = anchor.get("operation", "")
        if op == "create_node":
            if anchor.get("node_type") not in allowed_nodes:
                log.debug(
                    "executor.anchor_out_of_scope",
                    lens_id=lens.lens_id,
                    node_type=anchor.get("node_type"),
                )
                continue
        elif op == "create_edge":
            if anchor.get("edge_type") not in allowed_edges:
                log.debug(
                    "executor.anchor_out_of_scope",
                    lens_id=lens.lens_id,
                    edge_type=anchor.get("edge_type"),
                )
                continue
        filtered.append(anchor)

    return filtered
