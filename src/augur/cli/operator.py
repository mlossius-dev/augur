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


# ── augur ingest ──────────────────────────────────────────────────────────────


@app.command("ingest")
def ingest(
    source_id: Annotated[
        str | None,
        typer.Option("--source", "-s", help="Run ingestion for one source only"),
    ] = None,
) -> None:
    """
    Run ingestion pipeline: fetch payloads from all (or one) enabled source(s).

    Runs synchronously in the foreground — useful for testing a source or
    manually triggering a cycle outside the scheduler.
    """
    _run(_ingest_async(source_id))


async def _ingest_async(source_id: str | None) -> None:
    from augur.config import get_settings
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.ingestion.pipeline import IngestionPipeline
    from augur.ingestion.source_registry import get_enabled_sources, load_sources

    settings = get_settings()
    configure_logging(settings.log_level, "text")

    await init_db(settings)
    pool = get_raw_pool()

    pipeline = IngestionPipeline(
        pool,
        archive_root=settings.payload_archive_root,
        searxng_url=str(settings.searxng_url) if settings.searxng_url else "",
        sources_path=settings.sources_config_path or None,
    )

    try:
        if source_id:
            sources = [s for s in load_sources() if s.source_id == source_id]
            if not sources:
                typer.secho(f"Unknown source: {source_id!r}", fg=typer.colors.RED)
                raise typer.Exit(1)
            n = await pipeline.run_source(sources[0])
            typer.secho(f"Stored {n} payloads from {source_id}.", fg=typer.colors.GREEN)
        else:
            summary = await pipeline.run_all()
            for sid, n in sorted(summary.items()):
                color = typer.colors.GREEN if n > 0 else typer.colors.YELLOW
                typer.secho(f"  {sid}: {n} payloads", fg=color)
            typer.secho(f"\nTotal: {sum(summary.values())} payloads stored.", bold=True)
    finally:
        await close_db()


# ── augur inspect-payloads ────────────────────────────────────────────────────


@app.command("inspect-payloads")
def inspect_payloads(
    source_id: Annotated[
        str | None,
        typer.Option("--source", "-s", help="Filter by source_id"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
    show_content: Annotated[bool, typer.Option("--content")] = False,
) -> None:
    """
    List recently ingested payloads.

    Useful for verifying that ingestion is working correctly.
    """
    _run(_inspect_payloads_async(source_id, limit, show_content))


async def _inspect_payloads_async(
    source_id: str | None, limit: int, show_content: bool
) -> None:
    from augur.config import get_settings
    from augur.db.connection import close_db, get_raw_pool, init_db

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        conditions = ["NOT rejected"]
        params: list = [limit]
        if source_id:
            conditions.append(f"source_id = ${len(params) + 1}")
            params.append(source_id)

        where = "WHERE " + " AND ".join(conditions)
        sql = (
            f"SELECT payload_id, source_id, perspective, content_timestamp, "
            f"content_type, length(content) AS content_len, fetched_at "
            f"FROM payloads {where} ORDER BY fetched_at DESC LIMIT $1"
        )

        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)

        if not rows:
            typer.echo("No payloads found.")
            return

        typer.secho(f"\n{'ID':36}  {'SOURCE':25}  {'PERSPECTIVE':12}  {'CONTENT_TS':20}  CHARS", bold=True)
        typer.echo("─" * 110)
        for row in rows:
            typer.echo(
                f"{str(row['payload_id']):36}  "
                f"{row['source_id']:25}  "
                f"{row['perspective']:12}  "
                f"{str(row['content_timestamp'])[:19]:20}  "
                f"{row['content_len']}"
            )

        if show_content and rows:
            typer.secho("\nFirst payload content:", bold=True)
            async with pool.acquire() as conn:
                full = await conn.fetchrow(
                    "SELECT content FROM payloads WHERE payload_id = $1",
                    rows[0]["payload_id"],
                )
            if full:
                typer.echo(full["content"][:2000])
    finally:
        await close_db()


# ── augur inspect-signals ─────────────────────────────────────────────────────


@app.command("inspect-signals")
def inspect_signals(
    lens_id: Annotated[
        str | None,
        typer.Option("--lens", "-l", help="Filter by lens_id (e.g. commodities)"),
    ] = None,
    source_id: Annotated[
        str | None,
        typer.Option("--source", "-s", help="Filter by originating source_id"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
    show_anchors: Annotated[bool, typer.Option("--anchors")] = False,
) -> None:
    """
    List recent signals from Tier A.

    Pass --anchors to print the proposed_anchors for each signal.
    """
    _run(_inspect_signals_async(lens_id, source_id, limit, show_anchors))


async def _inspect_signals_async(
    lens_id: str | None, source_id: str | None, limit: int, show_anchors: bool
) -> None:
    from augur.config import get_settings
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.extraction.tier_a import TierAStore

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        tier_a = TierAStore(pool)
        signals = await tier_a.get_recent(lens_id=lens_id, source_id=source_id, limit=limit)

        if not signals:
            typer.echo("No signals found.")
            return

        typer.secho(f"\n{'SIGNAL_ID':36}  {'LENS':12}  {'CONF':15}  {'ANCHORS':7}  CLAIM", bold=True)
        typer.echo("─" * 120)
        for sig in signals:
            anchors = sig.get("proposed_anchors", [])
            typer.echo(
                f"{str(sig['signal_id']):36}  "
                f"{sig['lens_id']:12}  "
                f"{sig['confidence_band']:15}  "
                f"{len(anchors):7}  "
                f"{sig['claim_text'][:60]}"
            )
            if show_anchors and anchors:
                for anchor in anchors:
                    typer.secho(
                        f"    [{anchor.get('operation')}] {json.dumps(anchor)[:120]}",
                        fg=typer.colors.CYAN,
                    )
    finally:
        await close_db()


# ── augur extract ─────────────────────────────────────────────────────────────


@app.command("extract")
def extract(
    payload_id: Annotated[
        str | None,
        typer.Option("--payload", "-p", help="Extract signals from one specific payload UUID"),
    ] = None,
    lens_id: Annotated[
        str,
        typer.Option("--lens", "-l", help="Lens to use (default: commodities)"),
    ] = "commodities",
    hours: Annotated[
        int,
        typer.Option("--hours", "-h", help="Look back N hours for unprocessed payloads"),
    ] = 2,
) -> None:
    """
    Run the extraction lens over recent (or a specific) payload.

    Useful for testing the commodities lens manually against real content.
    """
    _run(_extract_async(payload_id, lens_id, hours))


async def _extract_async(
    payload_id_str: str | None, lens_id: str, hours: int
) -> None:
    from augur.config import get_settings
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.extraction.executor import LensExecutor
    from augur.extraction.lenses import ACTIVE_LENSES
    from augur.extraction.tier_a import TierAStore
    from augur.llm.client import LLMClient

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    lenses_map = {l.lens_id: l for l in ACTIVE_LENSES}
    lens = lenses_map.get(lens_id)
    if lens is None:
        typer.secho(f"Unknown lens: {lens_id!r}. Available: {list(lenses_map)}", fg=typer.colors.RED)
        raise typer.Exit(1)

    llm = LLMClient.from_settings(settings)
    executor = LensExecutor(llm)
    tier_a = TierAStore(pool)

    try:
        async with pool.acquire() as conn:
            if payload_id_str:
                import uuid as _uuid
                rows = await conn.fetch(
                    "SELECT payload_id, content, content_timestamp, source_id FROM payloads WHERE payload_id = $1",
                    _uuid.UUID(payload_id_str),
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT p.payload_id, p.content, p.content_timestamp, p.source_id
                    FROM payloads p
                    WHERE p.fetched_at > now() - $1::interval
                      AND NOT p.rejected
                      AND NOT EXISTS (SELECT 1 FROM signals s WHERE s.payload_id = p.payload_id)
                    ORDER BY p.content_timestamp DESC LIMIT 10
                    """,
                    f"{hours} hours",
                )

        if not rows:
            typer.echo("No payloads to process.")
            return

        typer.echo(f"Extracting signals from {len(rows)} payload(s) using lens={lens_id}…")
        total = 0
        for row in rows:
            signals = await executor.extract(
                payload_id=row["payload_id"],
                content=row["content"],
                content_timestamp=row["content_timestamp"],
                source_id=row["source_id"],
                lens=lens,
            )
            if signals:
                signals = await tier_a.deduplicate_batch(signals)
                stored = await tier_a.store_signals(signals)
                total += stored
                typer.secho(f"  {row['payload_id']}: {stored} signals stored", fg=typer.colors.GREEN)
            else:
                typer.echo(f"  {row['payload_id']}: no signals extracted")

        typer.secho(f"\nTotal: {total} signals stored.", bold=True)
    finally:
        await close_db()


# ── augur list-sources ─────────────────────────────────────────────────────────


@app.command("list-sources")
def list_sources(
    enabled_only: Annotated[bool, typer.Option("--enabled/--all")] = True,
) -> None:
    """List configured sources from the source registry."""
    from augur.ingestion.source_registry import get_enabled_sources, load_sources

    sources = get_enabled_sources() if enabled_only else load_sources()

    if not sources:
        typer.echo("No sources found.")
        return

    typer.secho(f"\n{'SOURCE_ID':30}  {'TIER':12}  {'PERSP':12}  {'METHOD':10}  {'WEIGHT':6}  {'CADENCE'}", bold=True)
    typer.echo("─" * 100)
    for s in sources:
        tier_label = f"tier-{s.tier}" if s.tier != "structured_data" else "struct"
        typer.echo(
            f"{s.source_id:30}  "
            f"{tier_label:12}  "
            f"{s.perspective:12}  "
            f"{s.access_method:10}  "
            f"{s.starting_source_weight:.2f}    "
            f"{s.update_cadence}"
        )
    typer.echo(f"\n{len(sources)} source(s) listed.")


# ── augur anchor ──────────────────────────────────────────────────────────────


@app.command("anchor")
def anchor(
    lens_id: Annotated[str | None, typer.Option("--lens", help="Only anchor signals from this lens")] = None,
    min_age: Annotated[int, typer.Option("--min-age", help="Min signal age in hours")] = 1,
    limit: Annotated[int, typer.Option("--limit")] = 200,
    force: Annotated[bool, typer.Option("--force/--no-force", help="Include under-sized batches")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Parse batches but do not apply")] = False,
) -> None:
    """
    Manually trigger one anchoring cycle.

    Pulls unanchored signals from Tier A, forms topical batches, calls the
    anchoring LLM, and applies the resulting graph operations via the Applier.
    """
    _run(_anchor_async(lens_id, min_age, limit, force, dry_run))


async def _anchor_async(
    lens_id: str | None,
    min_age: int,
    limit: int,
    force: bool,
    dry_run: bool,
) -> None:
    from augur.anchoring.orchestrator import AnchoringOrchestrator
    from augur.anchoring.batch_former import form_batches
    from augur.extraction.tier_a import TierAStore
    from augur.db.connection import init_db, close_db, get_raw_pool
    from augur.llm.client import LLMClient

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        if dry_run:
            tier_a = TierAStore(pool)
            signals = await tier_a.get_unanchored(lens_id=lens_id, min_age_hours=min_age, limit=limit)
            if not signals:
                typer.echo("No unanchored signals found.")
                return
            batches = form_batches(signals, force=force)
            typer.secho(f"\nDry run: {len(signals)} signal(s) → {len(batches)} batch(es)", bold=True)
            for i, batch in enumerate(batches, 1):
                typer.secho(f"\n  Batch {i} ({batch.batch_id})", fg=typer.colors.CYAN)
                typer.echo(f"    Lens IDs:  {', '.join(batch.lens_ids) or '(mixed)'}")
                typer.echo(f"    Signals:   {len(batch.signals)}")
                for sig in batch.signals[:3]:
                    typer.echo(f"      • {sig['claim_text'][:90]}")
                if len(batch.signals) > 3:
                    typer.echo(f"      … and {len(batch.signals) - 3} more")
            return

        llm = LLMClient.from_settings(settings)
        orchestrator = AnchoringOrchestrator(pool, llm)

        typer.echo(f"Starting anchoring cycle (lens={lens_id or 'all'}, min_age={min_age}h, force={force})…")
        result = await orchestrator.run_cycle(
            lens_id=lens_id, min_age_hours=min_age, limit=limit, force=force
        )

        typer.secho(f"\nAnchoring cycle complete", bold=True)
        typer.echo(f"  Batches processed:  {result.n_batches}")
        typer.echo(f"  Signals anchored:   {result.n_signals_processed}")
        typer.echo(f"  Operations applied: {result.n_applied}")
        typer.echo(f"  Operations rejected:{result.n_rejected}")

        for br in result.batch_results:
            color = typer.colors.GREEN if not br.llm_error and not br.parse_error else typer.colors.YELLOW
            typer.secho(
                f"\n  Batch {br.batch_id}: "
                f"{br.n_signals} signals → "
                f"+{br.n_applied} applied, "
                f"-{br.n_rejected} rejected",
                fg=color,
            )
            if br.llm_error:
                typer.secho(f"    LLM error: {br.llm_error}", fg=typer.colors.RED)
            if br.parse_error:
                typer.secho(f"    Parse warning: {br.parse_error}", fg=typer.colors.YELLOW)
    finally:
        await close_db()


# ── augur inspect-anchoring ───────────────────────────────────────────────────


@app.command("inspect-anchoring")
def inspect_anchoring(
    limit: Annotated[int, typer.Option("--limit")] = 20,
    lens_id: Annotated[str | None, typer.Option("--lens")] = None,
    pending: Annotated[bool, typer.Option("--pending/--all", help="Show only unanchored signals")] = True,
) -> None:
    """
    Inspect signals pending anchoring (or all recent signals).

    Shows claim text, confidence, proposed anchor operations, and anchor status.
    """
    _run(_inspect_anchoring_async(limit, lens_id, pending))


async def _inspect_anchoring_async(limit: int, lens_id: str | None, pending: bool) -> None:
    from augur.extraction.tier_a import TierAStore
    from augur.db.connection import init_db, close_db, get_raw_pool

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        tier_a = TierAStore(pool)

        if pending:
            signals = await tier_a.get_unanchored(lens_id=lens_id, min_age_hours=0, limit=limit)
            header = f"Unanchored signals (limit={limit})"
        else:
            signals = await tier_a.get_recent(lens_id=lens_id, limit=limit)
            header = f"Recent signals (limit={limit})"

        typer.secho(f"\n{header}: {len(signals)} found\n", bold=True)

        for sig in signals:
            status = "anchored" if sig.get("anchored") else "PENDING"
            status_color = typer.colors.GREEN if sig.get("anchored") else typer.colors.YELLOW
            typer.secho(
                f"  [{status}] {sig['signal_id']} ({sig['lens_id']})",
                fg=status_color,
            )
            typer.echo(f"    Claim:      {sig['claim_text'][:120]}")
            typer.echo(f"    Confidence: {sig.get('confidence_band', '?')}")
            typer.echo(f"    Timestamp:  {sig.get('content_timestamp', '?')}")
            anchors = sig.get("proposed_anchors", [])
            if anchors:
                typer.echo(f"    Anchors ({len(anchors)}):")
                for a in anchors[:5]:
                    op = a.get("operation", "?")
                    if op == "create_node":
                        desc = f"create_node {a.get('node_type')} '{a.get('fields', {}).get('name', '?')}'"
                    elif op == "create_edge":
                        desc = f"create_edge {a.get('source_node_id')} --{a.get('edge_type')}--> {a.get('target_node_id')}"
                    else:
                        desc = f"{op}"
                    typer.echo(f"      • {desc}")
                if len(anchors) > 5:
                    typer.echo(f"      … and {len(anchors) - 5} more")
            typer.echo("")
    finally:
        await close_db()


# ── augur disconfirm ──────────────────────────────────────────────────────────


@app.command("disconfirm")
def disconfirm(
    limit: Annotated[int, typer.Option("--limit", help="Max edges to challenge")] = 20,
    rechallenge_days: Annotated[int, typer.Option("--rechallenge-days")] = 7,
    signal_window_days: Annotated[int, typer.Option("--signal-window-days")] = 7,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show selected edges, do not challenge")] = False,
) -> None:
    """
    Manually trigger one disconfirmation pass.

    Selects high-weight edges that haven't been recently challenged,
    calls the strong model to look for falsifying evidence in recent
    Tier A signals, and applies results through the Applier.
    """
    _run(_disconfirm_async(limit, rechallenge_days, signal_window_days, dry_run))


async def _disconfirm_async(
    limit: int,
    rechallenge_days: int,
    signal_window_days: int,
    dry_run: bool,
) -> None:
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.disconfirmation.orchestrator import DisconfirmationOrchestrator
    from augur.disconfirmation.selector import select_edges

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        if dry_run:
            edges = await select_edges(
                pool,
                limit=limit,
                rechallenge_days=rechallenge_days,
            )
            typer.secho(f"\nDry run: {len(edges)} edge(s) selected for challenge\n", bold=True)
            for e in edges:
                typer.secho(
                    f"  [{e['current_weight_band']}] "
                    f"{e['source_name']} --{e['edge_type']}--> {e['target_name']}",
                    fg=typer.colors.CYAN,
                )
                typer.echo(f"    Edge ID:           {e['edge_id']}")
                typer.echo(f"    Last challenged:   {e.get('last_disconfirmation_pass') or 'never'}")
                typer.echo(f"    Falsification:     {e.get('falsification_criteria', '')[:120]}")
                typer.echo("")
            return

        from augur.llm.client import LLMClient
        llm = LLMClient.from_settings(settings)
        orchestrator = DisconfirmationOrchestrator(pool, llm)

        typer.echo(
            f"Starting disconfirmation pass "
            f"(limit={limit}, rechallenge_days={rechallenge_days}, "
            f"signal_window={signal_window_days}d)…"
        )
        result = await orchestrator.run_pass(
            limit=limit,
            rechallenge_days=rechallenge_days,
            signal_window_days=signal_window_days,
        )

        typer.secho(f"\nDisconfirmation pass complete", bold=True)
        typer.echo(f"  Edges challenged:        {result.n_edges_challenged}")
        typer.echo(f"  Disconfirmation found:   {result.n_found}")
        typer.echo(f"  No disconfirmation:      {result.n_not_found}")
        typer.echo(f"  Errors:                  {result.n_error}")
        typer.echo(f"  Operations applied:      {result.n_operations_applied}")
        typer.echo(f"  Operations rejected:     {result.n_operations_rejected}")

        for er in result.edge_results:
            color = (
                typer.colors.RED if er.outcome == "found"
                else typer.colors.GREEN if er.outcome == "not_found"
                else typer.colors.YELLOW
            )
            typer.secho(
                f"\n  [{er.outcome.upper()}] Edge {er.edge_id}",
                fg=color,
            )
            if er.reasoning:
                typer.echo(f"    {er.reasoning[:200]}")
            if er.llm_error:
                typer.secho(f"    LLM error: {er.llm_error}", fg=typer.colors.RED)
    finally:
        await close_db()


# ── augur inspect-disconfirmation ─────────────────────────────────────────────


@app.command("inspect-disconfirmation")
def inspect_disconfirmation(
    limit: Annotated[int, typer.Option("--limit")] = 20,
    outcome: Annotated[str | None, typer.Option("--outcome", help="found|not_found|error")] = None,
) -> None:
    """
    Review recent disconfirmation pass events.

    Shows which edges were challenged, the outcome, and the LLM reasoning.
    Use --outcome found to see edges that were weakened.
    """
    _run(_inspect_disconfirmation_async(limit, outcome))


async def _inspect_disconfirmation_async(limit: int, outcome: str | None) -> None:
    from augur.db.connection import close_db, get_raw_pool, init_db

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        async with pool.acquire() as conn:
            if outcome:
                rows = await conn.fetch(
                    """
                    SELECT dpe.*, e.current_weight_band,
                           sn.name AS source_name, tn.name AS target_name,
                           e.edge_type
                    FROM disconfirmation_pass_events dpe
                    JOIN edges e ON e.edge_id = dpe.edge_id
                    JOIN nodes sn ON sn.node_id = e.source_node_id
                    JOIN nodes tn ON tn.node_id = e.target_node_id
                    WHERE dpe.outcome = $1
                    ORDER BY dpe.challenged_at DESC LIMIT $2
                    """,
                    outcome, limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT dpe.*, e.current_weight_band,
                           sn.name AS source_name, tn.name AS target_name,
                           e.edge_type
                    FROM disconfirmation_pass_events dpe
                    JOIN edges e ON e.edge_id = dpe.edge_id
                    JOIN nodes sn ON sn.node_id = e.source_node_id
                    JOIN nodes tn ON tn.node_id = e.target_node_id
                    ORDER BY dpe.challenged_at DESC LIMIT $1
                    """,
                    limit,
                )

        typer.secho(f"\nDisconfirmation pass events ({len(rows)} found)\n", bold=True)
        for row in rows:
            oc = row["outcome"]
            color = (
                typer.colors.RED if oc == "found"
                else typer.colors.GREEN if oc == "not_found"
                else typer.colors.YELLOW
            )
            typer.secho(
                f"  [{oc.upper()}] "
                f"{row['source_name']} --{row['edge_type']}--> {row['target_name']} "
                f"[{row['current_weight_band']}]",
                fg=color,
            )
            typer.echo(f"    Edge:      {row['edge_id']}")
            typer.echo(f"    Challenged:{row['challenged_at'].strftime('%Y-%m-%d %H:%M UTC')}")
            typer.echo(f"    Signals reviewed: {len(row['signals_reviewed'])}")
            if row.get("reasoning"):
                typer.echo(f"    Reasoning: {row['reasoning'][:200]}")
            typer.echo("")
    finally:
        await close_db()


# ── augur calibrate ───────────────────────────────────────────────────────────

calibrate_app = typer.Typer(
    name="calibrate",
    help="Calibration run management — create, execute, score, report, apply.",
    add_completion=False,
)
app.add_typer(calibrate_app, name="calibrate")


@calibrate_app.command("create")
def calibrate_create(
    window_start: Annotated[str, typer.Option("--start", help="Window start date YYYY-MM-DD")] = "2022-09-01",
    window_end: Annotated[str, typer.Option("--end", help="Window end date YYYY-MM-DD")] = "2023-06-30",
    observation_days: Annotated[int, typer.Option("--obs-days")] = 90,
    sources: Annotated[str | None, typer.Option("--sources", help="Comma-separated source IDs")] = None,
    lenses: Annotated[str | None, typer.Option("--lenses", help="Comma-separated lens IDs")] = None,
    notes: Annotated[str, typer.Option("--notes")] = "",
) -> None:
    """
    Create a new calibration run (status=configured, not yet started).

    Defaults to the recommended first window: Sep 2022 – Jun 2023
    (European energy crisis arc).
    """
    _run(_calibrate_create_async(
        window_start, window_end, observation_days,
        sources.split(",") if sources else None,
        lenses.split(",") if lenses else None,
        notes,
    ))


async def _calibrate_create_async(
    window_start_str: str,
    window_end_str: str,
    observation_days: int,
    source_subset: list[str] | None,
    lens_subset: list[str] | None,
    notes: str,
) -> None:
    from datetime import datetime, timezone
    from augur.calibration.orchestrator import CalibrationOrchestrator
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.llm.client import LLMClient

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        window_start = datetime.fromisoformat(window_start_str).replace(tzinfo=timezone.utc)
        window_end = datetime.fromisoformat(window_end_str).replace(tzinfo=timezone.utc)

        llm = LLMClient.from_settings(settings)
        orch = CalibrationOrchestrator(pool, llm)
        run = await orch.create_run(
            window_start=window_start,
            window_end=window_end,
            observation_extension_days=observation_days,
            source_subset=source_subset,
            lens_subset=lens_subset,
            notes=notes,
        )
        typer.secho(f"\nCalibration run created: {run.run_id}", fg=typer.colors.GREEN, bold=True)
        typer.echo(f"  Window:       {window_start.date()} → {window_end.date()}")
        typer.echo(f"  Obs. ext:     {observation_days} days")
        typer.echo(f"  Sources:      {source_subset or 'all'}")
        typer.echo(f"  Lenses:       {lens_subset or 'all'}")
        typer.echo(f"  Status:       {run.status}")
        typer.echo(f"\nRun 'augur calibrate execute --run-id {run.run_id}' to start.")
    finally:
        await close_db()


@calibrate_app.command("execute")
def calibrate_execute(
    run_id: Annotated[str, typer.Option("--run-id", help="UUID of the calibration run")],
) -> None:
    """
    Execute a configured calibration run end-to-end.

    Phases: replay extraction → outcome resolution → leakage check → report.
    This may take a long time for large windows.
    """
    _run(_calibrate_execute_async(run_id))


async def _calibrate_execute_async(run_id_str: str) -> None:
    import uuid as _uuid
    from augur.calibration.orchestrator import CalibrationOrchestrator
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.llm.client import LLMClient

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        run_id = _uuid.UUID(run_id_str)
        llm = LLMClient.from_settings(settings)
        orch = CalibrationOrchestrator(pool, llm)

        run = await orch.get_run(run_id)
        if run is None:
            typer.secho(f"Run {run_id} not found.", fg=typer.colors.RED)
            raise typer.Exit(1)

        typer.echo(
            f"Executing calibration run {run.run_id} "
            f"({run.window_start.date()} → {run.window_end.date()})…"
        )
        run = await orch.execute_run(run)

        typer.secho(f"\nCalibration run complete: {run.run_id}", fg=typer.colors.GREEN, bold=True)
        if run.summary:
            typer.echo(f"  Signals total:   {run.summary.get('n_signals_total', '?')}")
            typer.echo(f"  Signals scored:  {run.summary.get('n_signals_scored', '?')}")
            leakage = run.summary.get('leakage_rate')
            if leakage is not None:
                color = typer.colors.YELLOW if leakage > 0.05 else typer.colors.GREEN
                typer.secho(f"  Leakage rate:    {leakage:.1%}", fg=color)
            typer.echo(f"  Flagged sources: {run.summary.get('flagged_sources', [])}")
            typer.echo(f"  Flagged lenses:  {run.summary.get('flagged_lenses', [])}")
    finally:
        await close_db()


@calibrate_app.command("report")
def calibrate_report(
    run_id: Annotated[str, typer.Option("--run-id", help="UUID of the calibration run")],
    format_: Annotated[str, typer.Option("--format", help="text|json")] = "text",
) -> None:
    """
    Print the calibration report for a completed run.

    Shows source scores (ordered by mean score), lens scores, leakage
    detection results, and flagged sources/lenses.
    """
    _run(_calibrate_report_async(run_id, format_))


async def _calibrate_report_async(run_id_str: str, format_: str) -> None:
    import uuid as _uuid
    from augur.calibration.orchestrator import CalibrationOrchestrator
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.llm.client import LLMClient

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        run_id = _uuid.UUID(run_id_str)
        llm = LLMClient.from_settings(settings)
        orch = CalibrationOrchestrator(pool, llm)

        run = await orch.get_run(run_id)
        if run is None:
            typer.secho(f"Run {run_id} not found.", fg=typer.colors.RED)
            raise typer.Exit(1)

        if not run.summary:
            typer.secho("Run has no summary yet. Has it completed?", fg=typer.colors.YELLOW)
            raise typer.Exit(1)

        if format_ == "json":
            typer.echo(json.dumps(run.summary, indent=2))
            return

        s = run.summary
        typer.secho(f"\nCalibration Report — Run {run.run_id}", bold=True)
        typer.echo(f"  Window:         {run.window_start.date()} → {run.window_end.date()}")
        typer.echo(f"  Signals total:  {s.get('n_signals_total', '?')}")
        typer.echo(f"  Signals scored: {s.get('n_signals_scored', '?')}")
        typer.echo(f"  Signals pending:{s.get('n_signals_pending', '?')}")
        leakage = s.get("leakage_rate")
        if leakage is not None:
            color = typer.colors.YELLOW if leakage > 0.05 else typer.colors.GREEN
            typer.secho(f"  Leakage rate:   {leakage:.1%}", fg=color)

        typer.secho(f"\n{'SOURCE':30}  {'TIER':5}  {'N_SIG':6}  {'MEAN':6}  {'PRIOR':6}  {'PROP':6}  {'Δ':6}", bold=True)
        typer.echo("─" * 80)
        for src in s.get("source_scores", []):
            delta = src["weight_delta"]
            color = (
                typer.colors.GREEN if delta > 0.05
                else typer.colors.RED if delta < -0.05
                else None
            )
            flag = " ⚑" if abs(delta) > 0.15 else ""
            typer.secho(
                f"  {src['source_id'][:28]:30}  "
                f"{src['tier']:5}  "
                f"{src['n_signals']:6}  "
                f"{src['mean_score']:+.3f}  "
                f"{src['prior_weight']:.3f}   "
                f"{src['proposed_weight']:.3f}  "
                f"{delta:+.3f}{flag}",
                fg=color,
            )

        typer.secho(f"\n{'LENS':30}  {'N_SIG':6}  {'MEAN':6}  {'STATUS'}", bold=True)
        typer.echo("─" * 70)
        for lens in s.get("lens_scores", []):
            color = typer.colors.YELLOW if lens.get("flagged") else None
            flag = " ⚑" if lens.get("flagged") else ""
            typer.secho(
                f"  {lens['lens_id']:30}  "
                f"{lens['n_signals']:6}  "
                f"{lens['mean_score']:+.3f}  "
                f"{'FLAGGED' if lens.get('flagged') else 'ok'}{flag}",
                fg=color,
            )

        if s.get("flagged_sources"):
            typer.secho(
                f"\n⚑ Flagged sources (weight Δ > 15%): {s['flagged_sources']}",
                fg=typer.colors.YELLOW,
            )
        if s.get("flagged_lenses"):
            typer.secho(
                f"⚑ Flagged lenses (mean_score < threshold): {s['flagged_lenses']}",
                fg=typer.colors.YELLOW,
            )
    finally:
        await close_db()


@calibrate_app.command("apply-weights")
def calibrate_apply_weights(
    run_id: Annotated[str, typer.Option("--run-id")],
    sources: Annotated[str | None, typer.Option("--sources", help="Comma-separated source IDs to apply")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """
    Apply proposed source weight updates from a completed calibration run.

    Operator approval gate — weights never apply automatically.
    Use --dry-run to preview proposed updates without applying them.
    """
    _run(_calibrate_apply_weights_async(run_id, sources, dry_run))


async def _calibrate_apply_weights_async(
    run_id_str: str,
    sources_str: str | None,
    dry_run: bool,
) -> None:
    import uuid as _uuid
    from augur.calibration.orchestrator import CalibrationOrchestrator
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.llm.client import LLMClient

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        run_id = _uuid.UUID(run_id_str)
        source_ids = sources_str.split(",") if sources_str else None

        llm = LLMClient.from_settings(settings)
        orch = CalibrationOrchestrator(pool, llm)
        run = await orch.get_run(run_id)
        if run is None:
            typer.secho(f"Run {run_id} not found.", fg=typer.colors.RED)
            raise typer.Exit(1)

        if not run.summary:
            typer.secho("Run has no summary yet.", fg=typer.colors.RED)
            raise typer.Exit(1)

        # Preview
        source_scores = run.summary.get("source_scores", [])
        applicable = [
            s for s in source_scores
            if source_ids is None or s["source_id"] in source_ids
        ]

        typer.secho(f"\nProposed weight updates ({len(applicable)} sources):\n", bold=True)
        for s in applicable:
            delta = s["weight_delta"]
            color = typer.colors.GREEN if delta > 0 else typer.colors.RED if delta < 0 else None
            typer.secho(
                f"  {s['source_id']:35}  "
                f"{s['prior_weight']:.3f} → {s['proposed_weight']:.3f}  "
                f"({delta:+.3f})",
                fg=color,
            )

        if dry_run:
            typer.echo("\n(Dry run — no changes applied.)")
            return

        updates = await orch.apply_weights(run, source_ids=source_ids)
        typer.secho(
            f"\n{len(updates)} source weight(s) logged for application.",
            fg=typer.colors.GREEN,
            bold=True,
        )
        typer.echo("Review and commit to sources.yaml to make changes permanent.")
    finally:
        await close_db()


@calibrate_app.command("list")
def calibrate_list(
    limit: Annotated[int, typer.Option("--limit")] = 10,
) -> None:
    """List recent calibration runs."""
    _run(_calibrate_list_async(limit))


async def _calibrate_list_async(limit: int) -> None:
    import uuid as _uuid
    from augur.calibration.orchestrator import CalibrationOrchestrator
    from augur.db.connection import close_db, get_raw_pool, init_db
    from augur.llm.client import LLMClient

    settings = get_settings()
    configure_logging(settings.log_level, "text")
    await init_db(settings)
    pool = get_raw_pool()

    try:
        llm = LLMClient.from_settings(settings)
        orch = CalibrationOrchestrator(pool, llm)
        runs = await orch.list_runs(limit=limit)

        if not runs:
            typer.echo("No calibration runs found.")
            return

        typer.secho(f"\n{'RUN_ID':38}  {'STATUS':12}  {'WINDOW':25}  {'CREATED'}", bold=True)
        typer.echo("─" * 100)
        for r in runs:
            color = {
                "complete": typer.colors.GREEN,
                "running": typer.colors.CYAN,
                "failed": typer.colors.RED,
            }.get(r.status.value)
            typer.secho(
                f"  {str(r.run_id):36}  "
                f"{r.status.value:12}  "
                f"{str(r.window_start.date())} → {str(r.window_end.date())}  "
                f"{r.created_at.strftime('%Y-%m-%d') if r.created_at else '?'}",
                fg=color,
            )
    finally:
        await close_db()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
