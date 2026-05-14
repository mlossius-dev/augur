"""
Parse and validate the LLM's JSON scenario output.

The LLM is instructed to return a raw JSON array.  This module parses it,
validates the required fields, and maps it to Scenario dataclasses.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

import structlog

from augur.projection.models import ProbabilityBand, Scenario

log = structlog.get_logger(__name__)

_VALID_BANDS = {b.value for b in ProbabilityBand}


def parse_scenarios(
    raw: str,
    *,
    dimension: str | None,
    model_used: str,
    as_of: datetime | None = None,
) -> tuple[list[Scenario], str | None]:
    """
    Parse the LLM output into Scenario objects.

    Returns (scenarios, error_message).  error_message is None on success.
    """
    as_of_ts = (as_of or datetime.now(timezone.utc)).isoformat()
    generated_at = datetime.now(timezone.utc).isoformat()

    # Strip accidental markdown fences
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", raw.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip(), flags=re.MULTILINE)
    cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        return [], f"JSON parse error: {e}"

    if not isinstance(data, list):
        return [], f"Expected JSON array, got {type(data).__name__}"

    scenarios: list[Scenario] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            log.warning("projection.parser.skip_non_dict", index=i)
            continue

        title = str(item.get("title", "")).strip()
        summary = str(item.get("summary", "")).strip()
        band_raw = str(item.get("probability_band", "")).lower().strip()
        horizon = str(item.get("time_horizon", "3–6 months")).strip()

        if not title or not summary:
            log.warning("projection.parser.skip_missing_fields", index=i)
            continue

        if band_raw not in _VALID_BANDS:
            band_raw = "moderate"

        scenarios.append(Scenario(
            scenario_id=str(uuid.uuid4()),
            dimension=dimension,
            title=title,
            summary=summary,
            probability_band=ProbabilityBand(band_raw),
            time_horizon=horizon,
            key_condition_ids=[],
            supporting_edge_ids=[],
            contradicting_edge_ids=[],
            generated_at=generated_at,
            as_of=as_of_ts,
            model_used=model_used,
        ))

    if not scenarios:
        return [], "No valid scenarios parsed from LLM output"

    return scenarios, None
