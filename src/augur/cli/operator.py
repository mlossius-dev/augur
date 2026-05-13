"""
Augur operator CLI.

A command-line interface for ad-hoc operator tasks: health checks, database
inspection, LLM test calls, and basic observability.

Install with:  pip install -e .
Run with:      augur <command>

All commands that need the database or LLM client initialise them inline
rather than relying on FastAPI's lifespan so they can be run independently
of the web server.
"""

from __future__ import annotations

import asyncio
import json
from typing import Annotated

import typer

from augur.logging import configure_logging

app = typer.Typer(
    name="augur",
    help="Augur operator CLI — infrastructure inspection and ad-hoc tasks.",
    add_completion=False,
)


def _run(coro):  # type: ignore[no-untyped-def]
    """Run an async function from a synchronous Typer callback."""
    return asyncio.run(coro)


# ── augur status ─────────────────────────────────────────────────────────────


@app.command()
def status() -> None:
    """
    Print the health status of all infrastructure components:
    database (pgvector + AGE), Langfuse, OpenRouter keys.

    Corresponds to Phase 0 success criteria:
      - DB returns expected results exercising pgvector and AGE.
      - LLM call through the client abstraction shows up in Langfuse.
    """
    _run(_status_async())


async def _status_async() -> None:
    from augur.config import get_settings
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.llm.client import LLMClient
    from augur.logging import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, "text")

    typer.secho("\nAugur infrastructure status", bold=True)
    typer.echo("─" * 40)

    # ── Database ──────────────────────────────────────────────────────────────
    typer.echo("\n[Database]")
    try:
        await init_db(settings)
        pool = get_raw_pool()
        async with pool.acquire() as conn:
            extensions = await conn.fetch(
                "SELECT extname, extversion FROM pg_extension "
                "WHERE extname = ANY($1)",
                ["vector", "pg_trgm", "postgis", "age"],
            )
            found = {r["extname"] for r in extensions}
            for ext in ["vector", "pg_trgm", "postgis", "age"]:
                mark = "✓" if ext in found else "✗"
                typer.echo(f"  {mark} {ext}")

        await close_db()
        typer.secho("  Database: OK", fg=typer.colors.GREEN)
    except Exception as exc:
        typer.secho(f"  Database: FAILED — {exc}", fg=typer.colors.RED)

    # ── LLM client ────────────────────────────────────────────────────────────
    typer.echo("\n[LLM / Langfuse]")
    try:
        client = LLMClient.from_settings(settings)
        health = await client.health_check()
        for key, value in health.items():
            mark = "✓" if value not in (False, None, "") else "✗"
            typer.echo(f"  {mark} {key}: {value}")
        typer.secho("  LLM client: OK", fg=typer.colors.GREEN)
    except Exception as exc:
        typer.secho(f"  LLM client: FAILED — {exc}", fg=typer.colors.RED)

    typer.echo()


# ── augur db migrate ─────────────────────────────────────────────────────────


@app.command()
def migrate() -> None:
    """
    Apply any pending database migrations.

    Safe to run multiple times; already-applied migrations are skipped.
    """
    _run(_migrate_async())


async def _migrate_async() -> None:
    from pathlib import Path

    from augur.config import get_settings
    from augur.db.connection import close_db, get_raw_pool, init_db

    settings = get_settings()
    configure_logging(settings.log_level, "text")

    typer.echo("Running migrations…")
    await init_db(settings)

    migrations_dir = Path(__file__).parent.parent / "db" / "migrations"
    migration_files = sorted(migrations_dir.glob("*.sql"))

    pool = get_raw_pool()
    applied_count = 0

    async with pool.acquire() as conn:
        for f in migration_files:
            version = f.stem.split("_")[0]
            already = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version = $1)",
                version,
            )
            if already:
                typer.echo(f"  skip  {f.name}")
                continue
            typer.echo(f"  apply {f.name}")
            await conn.execute(f.read_text())
            applied_count += 1

    await close_db()

    if applied_count == 0:
        typer.echo("No new migrations to apply.")
    else:
        typer.secho(f"Applied {applied_count} migration(s).", fg=typer.colors.GREEN)


# ── augur llm test ────────────────────────────────────────────────────────────


@app.command("llm-test")
def llm_test(
    stage: Annotated[
        str,
        typer.Option(
            "--stage",
            help="Pipeline stage (extraction|anchoring|disconfirmation|projection|conversation)",
        ),
    ] = "extraction",
    prompt: Annotated[
        str,
        typer.Option("--prompt", help="Test prompt to send"),
    ] = "Say 'Augur LLM test OK' and nothing else.",
) -> None:
    """
    Make a test LLM call through the client abstraction.

    Phase 0 success criterion: a test call shows up in Langfuse.

    The call uses the cheapest model for the selected stage.
    """
    _run(_llm_test_async(stage, prompt))


async def _llm_test_async(stage_str: str, prompt: str) -> None:
    from augur.config import get_settings
    from augur.llm.client import LLMClient
    from augur.llm.models import PipelineStage

    settings = get_settings()
    configure_logging(settings.log_level, "text")

    try:
        stage = PipelineStage(stage_str)
    except ValueError:
        typer.secho(
            f"Unknown stage '{stage_str}'. Valid values: "
            + ", ".join(s.value for s in PipelineStage),
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    client = LLMClient.from_settings(settings)
    typer.echo(f"Calling OpenRouter stage={stage} model={client.model_for_stage(stage)} …")

    try:
        response = await client.complete(
            stage=stage,
            prompt_template_id="operator_test_v1",
            messages=[{"role": "user", "content": prompt}],
            metadata={"source": "operator_cli"},
        )
        typer.secho("\nResponse:", bold=True)
        typer.echo(f"  {response.content}")
        typer.echo(f"\n  model            : {response.model}")
        typer.echo(f"  prompt_tokens    : {response.prompt_tokens}")
        typer.echo(f"  completion_tokens: {response.completion_tokens}")
        typer.secho(f"  langfuse_trace   : {response.langfuse_trace_id}", fg=typer.colors.CYAN)
        typer.secho("\nLLM test: OK", fg=typer.colors.GREEN)
    except Exception as exc:
        typer.secho(f"\nLLM test FAILED: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1)


# ── augur db query ────────────────────────────────────────────────────────────


@app.command("db-query")
def db_query(
    sql: Annotated[str, typer.Argument(help="SQL query to execute")],
) -> None:
    """Execute a raw SQL query and print the results as JSON. For debugging."""
    _run(_db_query_async(sql))


async def _db_query_async(sql: str) -> None:
    from augur.config import get_settings
    from augur.db.connection import close_db, get_raw_pool, init_db

    settings = get_settings()
    configure_logging(settings.log_level, "text")

    await init_db(settings)
    pool = get_raw_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)

    await close_db()

    if not rows:
        typer.echo("(no rows)")
        return

    result = [dict(r) for r in rows]
    typer.echo(json.dumps(result, indent=2, default=str))


# ── augur graph load-seed ─────────────────────────────────────────────────────


@app.command("load-seed")
def load_seed(
    aliases_only: Annotated[
        bool,
        typer.Option("--aliases-only", help="Load only alias seeds; skip the graph seed"),
    ] = False,
    graph_only: Annotated[
        bool,
        typer.Option("--graph-only", help="Load only the graph seed; skip alias loading"),
    ] = False,
) -> None:
    """
    Load Phase 1 seed data: alias table + fertilizer→food chain graph.

    Idempotent: safe to run multiple times.  Existing aliases are skipped;
    existing entity nodes are alias-rewritten rather than duplicated.
    """
    _run(_load_seed_async(aliases_only=aliases_only, graph_only=graph_only))


async def _load_seed_async(*, aliases_only: bool, graph_only: bool) -> None:
    from augur.config import get_settings
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.seeds.aliases_seed import load_aliases
    from augur.seeds.seed_graph import load_seed_graph

    settings = get_settings()
    configure_logging(settings.log_level, "text")

    await init_db(settings)
    pool = get_raw_pool()

    try:
        if not graph_only:
            typer.echo("Loading alias seeds…")
            inserted = await load_aliases(pool)
            typer.secho(f"  Aliases loaded: {inserted} new entries", fg=typer.colors.GREEN)

        if not aliases_only:
            typer.echo("Loading seed graph (fertilizer → food chain)…")
            counts = await load_seed_graph(pool)
            typer.secho(
                f"  Graph seed: {counts['applied']} applied, {counts['rejected']} rejected",
                fg=typer.colors.GREEN if counts["rejected"] == 0 else typer.colors.YELLOW,
            )
    finally:
        await close_db()


# ── augur graph verify ────────────────────────────────────────────────────────


@app.command("verify-graph")
def verify_graph() -> None:
    """
    Verify that the seed graph loaded correctly.

    Checks node counts, edge counts, and that the fertilizer→food chain
    core path exists.  Exits non-zero if verification fails.
    """
    _run(_verify_graph_async())


async def _verify_graph_async() -> None:
    from augur.config import get_settings
    from augur.db.connection import close_db, get_raw_pool, init_db

    settings = get_settings()
    configure_logging(settings.log_level, "text")

    await init_db(settings)
    pool = get_raw_pool()
    ok = True

    try:
        async with pool.acquire() as conn:
            # Basic counts
            node_count = await conn.fetchval("SELECT COUNT(*) FROM nodes")
            edge_count = await conn.fetchval(
                "SELECT COUNT(*) FROM edges WHERE NOT deprecated"
            )
            alias_count = await conn.fetchval("SELECT COUNT(*) FROM aliases")

            typer.secho("\nGraph verification", bold=True)
            typer.echo("─" * 40)
            typer.echo(f"  nodes  : {node_count}")
            typer.echo(f"  edges  : {edge_count}")
            typer.echo(f"  aliases: {alias_count}")

            # Check for the fertilizer chain core entity
            gas_node = await conn.fetchval(
                "SELECT node_id FROM nodes WHERE lower(name) = 'natural gas supply' LIMIT 1"
            )
            food_node = await conn.fetchval(
                "SELECT node_id FROM nodes WHERE lower(name) = 'food security' LIMIT 1"
            )

            typer.echo("\n  Seed graph entities:")
            for label, result in [("Natural Gas Supply", gas_node), ("Food Security", food_node)]:
                mark = "✓" if result else "✗"
                color = typer.colors.GREEN if result else typer.colors.RED
                typer.secho(f"    {mark} {label}", fg=color)
                if not result:
                    ok = False

            # Schema_migrations sanity check
            migrations = await conn.fetch(
                "SELECT version, description FROM schema_migrations ORDER BY version"
            )
            typer.echo("\n  Applied migrations:")
            for row in migrations:
                typer.echo(f"    ✓ {row['version']} — {row['description']}")

    finally:
        await close_db()

    if ok:
        typer.secho("\nVerification passed.", fg=typer.colors.GREEN)
    else:
        typer.secho("\nVerification FAILED.", fg=typer.colors.RED)
        raise typer.Exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
