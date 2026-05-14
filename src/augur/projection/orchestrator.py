"""
Projection orchestrator: gather evidence → call LLM → parse → persist.
"""

from __future__ import annotations

from datetime import datetime

import asyncpg
import structlog

from augur.llm.client import LLMClient
from augur.llm.models import PipelineStage
from augur.presentation.dimensions import DIMENSION_LABELS, DIMENSIONS
from augur.projection.evidence import gather_evidence
from augur.projection.models import ProjectionResult
from augur.projection.parser import parse_scenarios
from augur.projection.prompts import SYSTEM_PROMPT, build_user_message
from augur.projection.store import save_scenarios

log = structlog.get_logger(__name__)


class ProjectionOrchestrator:
    def __init__(self, pool: asyncpg.Pool, llm_client: LLMClient) -> None:
        self._pool = pool
        self._llm = llm_client

    async def run_projection(
        self,
        *,
        dimension: str | None = None,
        as_of: datetime | None = None,
        save: bool = True,
        deprecate_previous: bool = True,
    ) -> ProjectionResult:
        """
        Generate scenarios for one dimension (or all if dimension=None).

        Returns a ProjectionResult with all generated scenarios.
        """
        if dimension is not None and dimension not in DIMENSIONS:
            raise ValueError(f"Unknown dimension: {dimension!r}")

        dimension_label = DIMENSION_LABELS.get(dimension) if dimension else None

        log.info("projection.run_start", dimension=dimension)

        evidence = await gather_evidence(self._pool, dimension=dimension, as_of=as_of)

        user_msg = build_user_message(evidence, dimension_label=dimension_label)

        response = await self._llm.complete(
            stage=PipelineStage.PROJECTION,
            prompt_template_id="scenario_generation_v1",
            messages=[
                {"role": "user", "content": user_msg},
            ],
            system=SYSTEM_PROMPT,
            metadata={
                "dimension": dimension or "all",
                "n_conditions": len(evidence.active_conditions),
                "n_edges": len(evidence.strong_edges),
            },
        )

        scenarios, error = parse_scenarios(
            response.content,
            dimension=dimension,
            model_used=response.model,
            as_of=as_of,
        )

        if error:
            log.warning("projection.parse_error", error=error, dimension=dimension)

        if scenarios and save:
            await save_scenarios(
                self._pool,
                scenarios,
                deprecate_previous=deprecate_previous,
                dimension=dimension,
            )
            log.info(
                "projection.saved",
                n=len(scenarios),
                dimension=dimension,
            )

        return ProjectionResult(
            dimension=dimension,
            scenarios=scenarios,
            n_conditions_used=len(evidence.active_conditions),
            n_edges_used=len(evidence.strong_edges),
            model_used=response.model,
            as_of=(as_of or datetime.now()).isoformat(),
        )

    async def run_all_dimensions(
        self,
        *,
        as_of: datetime | None = None,
    ) -> list[ProjectionResult]:
        """Generate scenarios for each of the five dimensions sequentially."""
        results = []
        for dim in DIMENSIONS:
            try:
                result = await self.run_projection(dimension=dim, as_of=as_of)
                results.append(result)
            except Exception as exc:
                log.error("projection.dimension_failed", dimension=dim, error=str(exc))
        return results
