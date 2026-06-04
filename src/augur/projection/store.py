"""
Persistence for scenarios: save, retrieve, deprecate.
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
import structlog

from augur.projection.models import Scenario

log = structlog.get_logger(__name__)


async def save_scenarios(
    pool: asyncpg.Pool,
    scenarios: list[Scenario],
    *,
    deprecate_previous: bool = True,
    dimension: str | None = None,
) -> int:
    """
    Persist a batch of scenarios.

    If deprecate_previous=True, marks all existing non-deprecated scenarios
    for the same dimension as deprecated before inserting.

    Returns count of rows inserted.
    """
    if not scenarios:
        return 0

    async with pool.acquire() as conn:
        if deprecate_previous:
            await conn.execute(
                """
                UPDATE scenarios
                SET deprecated = TRUE
                WHERE NOT deprecated
                  AND (dimension = $1 OR ($1 IS NULL AND dimension IS NULL))
                """,
                dimension,
            )

        await conn.executemany(
            """
            INSERT INTO scenarios (
                scenario_id, dimension, title, summary, probability_band,
                time_horizon, key_condition_ids, supporting_edge_ids,
                contradicting_edge_ids, generated_at, as_of, model_used
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7::uuid[], $8::uuid[], $9::uuid[],
                $10::timestamptz, $11::timestamptz, $12
            )
            """,
            [
                (
                    s.scenario_id,
                    s.dimension,
                    s.title,
                    s.summary,
                    s.probability_band,
                    s.time_horizon,
                    s.key_condition_ids or [],
                    s.supporting_edge_ids or [],
                    s.contradicting_edge_ids or [],
                    s.generated_at,
                    s.as_of,
                    s.model_used,
                )
                for s in scenarios
            ],
        )

    return len(scenarios)


async def get_scenarios(
    pool: asyncpg.Pool,
    *,
    dimension: str | None = None,
    as_of: datetime | None = None,
    limit: int = 20,
    include_deprecated: bool = False,
) -> list[Scenario]:
    """
    Retrieve scenarios, optionally filtered by dimension.

    If as_of is provided, returns scenarios generated before that point.
    """
    from augur.projection.models import ProbabilityBand

    cutoff = as_of or datetime.now(timezone.utc)

    conditions = ["generated_at <= $1"]
    params: list = [cutoff]

    if not include_deprecated:
        conditions.append("NOT deprecated")

    if dimension is not None:
        conditions.append(f"dimension = ${len(params) + 1}")
        params.append(dimension)

    params.append(limit)
    where = " AND ".join(conditions)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT scenario_id, dimension, title, summary, probability_band,
                   time_horizon, key_condition_ids, supporting_edge_ids,
                   contradicting_edge_ids, generated_at, as_of, model_used, deprecated
            FROM scenarios
            WHERE {where}
            ORDER BY generated_at DESC, probability_band ASC
            LIMIT ${len(params)}
            """,
            *params,
        )

    return [
        Scenario(
            scenario_id=str(r["scenario_id"]),
            dimension=r["dimension"],
            title=r["title"],
            summary=r["summary"],
            probability_band=ProbabilityBand(r["probability_band"]),
            time_horizon=r["time_horizon"],
            key_condition_ids=[str(i) for i in (r["key_condition_ids"] or [])],
            supporting_edge_ids=[str(i) for i in (r["supporting_edge_ids"] or [])],
            contradicting_edge_ids=[str(i) for i in (r["contradicting_edge_ids"] or [])],
            generated_at=r["generated_at"].isoformat(),
            as_of=r["as_of"].isoformat(),
            model_used=r["model_used"],
            deprecated=r["deprecated"],
        )
        for r in rows
    ]
