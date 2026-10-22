# Augur — Architecture

*The shape of the system: components, data flow, and the contracts between them. Inherits from `augur-vision.md`. Implementation details for individual stages live in `augur-signal-pipeline.md`.*

---

## Reading guide

This document describes **what the system is**, not **how each piece is implemented**. Where a stage has its own complexity (lens prompt design, anchoring logic, weight update math), the document points to the specialized file that owns it.

Cross-references:
- Vision and design principles → `augur-vision.md`
- Source registry and tiering → `augur-sources.md`
- Graph node and edge schema → `augur-graph-schema.md`
- Pipeline stage internals → `augur-signal-pipeline.md`
- Build sequence → `augur-roadmap.md`
- Signal-extraction subsystem → `augur-signal-pipeline.md`
- Sibling system Augur Discourse and the `core_bridge` contract → "Sibling systems" below

---

## Deployment context

Augur runs as a containerized application on a single Hetzner VPS at the start. The design assumes:

- One node, modest resources (no Kubernetes, no horizontal scaling).
- Docker Compose for orchestration.
- LLM inference is **always external** via OpenRouter. The VPS never runs models locally.
- Storage is local to the VPS, with regular off-host backups.
- The system can be paused, restarted, and migrated without losing graph state.

The architecture is not designed to scale to many users. It is designed to run reliably for one operator and a handful of trusted collaborators.

### Existing infrastructure on the VPS

One service is already running on the target host and is treated as an architectural dependency rather than something Augur deploys itself:

- **SearXNG** — meta-search instance used by the ingestion layer for general-purpose web search across regions and language pools. Source-registry and search-strategy details live in `augur-sources.md`.

It is treated as stable infrastructure. If it becomes unavailable, ingestion degrades, but the rest of the system continues to operate against its existing graph state.

LLM observability needs no separate service: every LLM call is recorded in Augur's own database (see the observability section below). An earlier design used Langfuse for this; it was dropped to keep the host to one database and because the useful questions about LLM calls are joins against the graph.

---

## Component overview

```
                          ┌─────────────────────────────────────┐
                          │           SOURCE LAYER              │
                          │   (news, APIs, structured feeds)    │
                          │   → augur-sources.md for registry   │
                          └──────────────────┬──────────────────┘
                                             │
                                             ▼
                          ┌─────────────────────────────────────┐
                          │         INGESTION LAYER             │
                          │  fetchers, scrapers, API clients,   │
                          │  raw payload normalization          │
                          └──────────────────┬──────────────────┘
                                             │
                                             ▼
              ┌──────────────────────────────────────────────────────┐
              │              EXTRACTION LAYER                        │
              │   multi-lens parallel signal extraction (LLM)        │
              │   → augur-signal-pipeline.md for lens internals      │
              └──────────────────────────┬───────────────────────────┘
                                         │
                                         ▼
              ┌──────────────────────────────────────────────────────┐
              │           TIER A:  RAW SIGNAL STORE                  │
              │   structured signals + claim vectors (90-day TTL)    │
              │   deduplication, clustering, convergence detection   │
              └──────────────────────────┬───────────────────────────┘
                                         │
                                         ▼
              ┌──────────────────────────────────────────────────────┐
              │               ANCHORING LAYER                        │
              │   LLM proposes graph updates, system applies them    │
              │   deterministically against current graph state      │
              └──────────────────────────┬───────────────────────────┘
                                         │
                                         ▼
              ┌──────────────────────────────────────────────────────┐
              │           TIER B:  GRAPH STATE STORE                 │
              │   nodes, edges, weight history, source provenance    │
              │   → augur-graph-schema.md for schema                 │
              └──────────────────────────┬───────────────────────────┘
                                         │
                       ┌─────────────────┼─────────────────┐
                       │                 │                 │
                       ▼                 ▼                 ▼
              ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
              │ DISCONFIRMATION│ │   PROJECTION   │ │   READ /UI     │
              │  periodic pass │ │  graph walks,  │ │  navigation,   │
              │  against high- │ │  conditional   │ │  inspection,   │
              │  weight edges  │ │  branches      │ │  user edits    │
              └────────────────┘ └────────────────┘ └────────────────┘
```

---

## The two-tier memory model

The single most important architectural decision is splitting memory into two tiers with different retention rules and different purposes.

### Tier A — Raw Signal Store

**Purpose:** working memory. Used during ingestion to detect duplicates, cluster related observations, and identify when multiple sources are converging on the same claim.

**What's stored:**
- The structured signal record: claim text, source reference, ingestion time, originating perspective, confidence band, proposed graph anchors.
- A vector embedding of the *claim text only* (not the full article body).
- The link back to the originating raw payload in the ingestion archive.

**Retention:** ~90 days hot. After 90 days, a signal is either promoted to Tier B (because it anchored to the graph and the anchor record references it), or it expires.

**Why this exists:** the deduplication problem is local in time. When 200 outlets reprint a Reuters wire, you need to see all 200 within a few days and compress them to "one claim, five corroborating perspectives, 200 reprints." You do not need to query that data three years later. Aging it out aggressively is what keeps the vector layer small and useful.

**Why claim-level vectors, not article-level:** article embeddings are noisy and dominated by stylistic variance between outlets. The claim is the thing that matters for deduplication ("phosphate exports from Morocco declined in Q1"). Reducing to the claim level before vectorizing is what makes similarity search useful here.

### Tier B — Graph State Store

**Purpose:** long-term memory. The graph itself is the durable record of what Augur believes about the world and why.

**What's stored:**
- Nodes (entities, conditions, events) with metadata.
- Edges (causal relationships) with weights, supporting signal references, and reasoning text.
- Weight history per edge as a time series: every change to an edge weight is appended with a timestamp, the triggering signal IDs, and a reasoning string.
- Schema and lens definitions, versioned.

**Retention:** indefinite. Edge histories are small (kilobytes per edge per year) and they are the heart of the system.

**Why this exists:** the long-term memory is structured, not statistical. When a user asks "what did Augur think in March about Y," the answer comes from replaying the graph state at that point, not from querying old article vectors. This is what makes Augur navigable and auditable.

### The contract between tiers

A signal in Tier A becomes part of Tier B only through the **anchoring layer**. Tier A signals never directly write to the graph. The anchoring layer is the gate, and its outputs are first-class records (graph update events) that themselves live in Tier B as immutable history.

This separation matters because it makes the system debuggable. If the graph develops a bad edge, you can trace it back through the anchoring events to the originating signals, and from there to the originating sources.

---

## Storage stack

The graph and signal store both need to handle:
- Structured records with strong schema.
- Vector similarity search at the claim level.
- Graph traversal for projection (walking from a node N hops outward, conditional on edge weights).
- Time-series append-only history for edge weights.
- Geospatial queries for some signal types (shipping, seismic, mining locations).
- Concurrent reads during projection while writes happen during ingestion.

**Recommendation: PostgreSQL 16+ with the following extensions, in a single database:**

- `pgvector` — claim-level embeddings for Tier A deduplication and clustering.
- `pg_trgm` — fuzzy text matching for entity resolution during anchoring.
- `PostGIS` — geospatial indexing for location-tagged signals (shipping zones, seismic events, mining sites).
- `Apache AGE` — Cypher-compatible graph layer over Postgres, so the graph traversals stay in SQL/Cypher rather than requiring a separate graph database.
- `TimescaleDB` (optional, can be added later) — hypertable for edge-weight history if the time-series volume grows beyond what plain Postgres handles comfortably.

**Why one database instead of a polyglot stack:**

The temptation is to use Neo4j for the graph, Pinecone or Qdrant for vectors, Postgres for everything else. For a single-VPS deployment that needs to be debuggable, backed up consistently, and operated by one person, a single Postgres instance with the right extensions is dramatically simpler. The performance ceiling is high enough that Augur will not exceed it for years.

If the graph layer ever outgrows AGE, the migration path is to extract just the graph tables to a dedicated graph database, keeping everything else in Postgres. The schema is designed to make that migration tractable, but not to anticipate it prematurely.

**Why not Neo4j from the start:**

Neo4j is excellent for graphs but introduces a second persistence layer with different backup semantics, different transactional guarantees, and a separate query language to maintain. For a project where the graph is the core but not the only thing, the integration cost outweighs the per-query performance gain. AGE inside Postgres gives you Cypher when you want it and SQL when you don't, with one backup story.

**Object/blob storage:**

Raw ingested article payloads, archived after extraction, go to local filesystem first and S3-compatible object storage (Hetzner Object Storage or Backblaze B2) as a backup tier. These are write-once read-rarely and don't need to live in the database.

---

## Runtime stack

**Language:** Python 3.12+. The ecosystem fit for LLM orchestration, web scraping, data manipulation, and async I/O is unmatched, and it matches the rest of your existing project work.

**Framework:** FastAPI for the HTTP layer (read API, eventually UI backend, internal admin endpoints).

**Task orchestration:** the question is what runs ingestion, extraction, anchoring, and the disconfirmation pass on a schedule.

Three viable options:

1. **APScheduler in-process** — simplest. Scheduler runs in the same Python process as the API. Fine while the system is small. Risk: one crash takes down both scheduling and serving.
2. **Celery with Redis broker** — most familiar pattern. Robust, well-documented, but adds two more components (worker + broker) to the docker-compose file.
3. **Dramatiq with Redis or RabbitMQ** — lighter than Celery, simpler API, still gives you out-of-process workers.

**Recommendation: start with APScheduler in-process** for the first few weeks while the pipeline is being built and the load is trivial. Migrate to **Dramatiq with Redis** when there's a reason to (concurrent extractions backing up, scheduling reliability becoming a concern, disconfirmation passes contending with ingestion). The migration is small because the task functions are the same; only the invocation wrapper changes.

**LLM client layer:** a thin abstraction over the OpenRouter API that:

- Exposes a uniform `extract(prompt, schema)` interface regardless of which model is being called.
- Logs every call (model, prompt template ID, input tokens, output tokens, cost, latency, response).
- Supports per-stage model selection (cheap model for lens extraction, stronger model for anchoring and disconfirmation).
- Implements retries with exponential backoff, structured-output validation, and fallback model routing.
- **Records every call in `llm_calls`** before making it, and completes the row afterwards. See the observability section below.

Model selection is a **runtime configuration concern**, not an architectural one. The system should support swapping models per stage without code changes. As of writing, the rough mapping is:

| Stage | Model class | Why |
|---|---|---|
| Lens extraction | Small/fast (e.g., DeepSeek V4, Gemini Flash, Haiku) | Narrow task, high volume, cost dominates |
| Anchoring | Mid-strength | Needs structured reasoning over graph state |
| Disconfirmation | Strong (Claude Opus, GPT-5, or equivalent) | Quality matters more than cost; runs infrequently |
| Projection | Mid to strong | Coherent multi-step reasoning |

These are guidance, not commitments. The orchestration layer must make this a config decision.

**Current configuration (October 2026): free models only.** Every stage defaults to OpenRouter's free models router (`openrouter/free`), which picks an available free model per call; `AUGUR_MODEL_<STAGE>` pins a stage to a specific model, or to a comma-separated fallback chain (e.g. `vendor/a:free,vendor/b:free,openrouter/free`). A chain is sent as one request with OpenRouter's `models` parameter: if the first model errors (gated, rate-limited upstream, down), OpenRouter tries the next, and `llm_calls.model` records the one that answered. Free models come and go and are often rate-limited at their provider, so a single pinned model is fragile; ending a chain with `openrouter/free` keeps the stage running when every pinned model fails. Which free models suit which stage is runtime configuration, checked against OpenRouter's free list at deploy time, not something the code or this document fixes. The operator chose free models because the paid budget was too high for a system that is not a product, and accepts lower extraction and anchoring quality. The table above still describes what each stage would use if paid models return.

The free tier limits requests, not spend: 20 a minute and 1,000 a day per OpenRouter account (50 a day for accounts that have bought less than $10 of credits in total), shared with Augur Discourse. The LLM client therefore enforces a call quota as well as the dollar budget: `LLM_DAILY_CALL_LIMIT` (default 800) and `LLM_CALLS_PER_MINUTE` (default 12), counted from `llm_calls`, failed calls included. Hourly extraction is capped at `EXTRACTION_CALLS_PER_RUN` (default 20, about two payloads with all lenses), newest payloads first, so it cannot spend the quota that anchoring, disconfirmation and the reader-facing stages need. Coverage, not cost, is now the binding constraint: most ingested payloads are never read by a lens.

---

## Component contracts

Each layer in the diagram exposes a stable interface to the next, so that internal implementation can change without rippling through the system.

### Ingestion → Extraction

**Input to extraction:** a normalized payload object containing:
- `payload_id` (UUID)
- `source_id` (FK to source registry)
- `fetched_at` (UTC timestamp)
- `perspective` (US/EU, India, China, etc. — assigned by source registry)
- `content` (text body)
- `content_type` (article, API response, structured feed entry, etc.)
- `metadata` (URL, headline, publication date, language, raw response if API)

The ingestion layer is responsible for getting payloads into this shape. Anything below ingestion treats the payload as opaque except via these fields.

### Extraction → Tier A

**Output of extraction:** zero or more **signal records**, each with:
- `signal_id` (UUID)
- `payload_id` (FK)
- `lens_id` (which lens produced it)
- `claim_text` (concise, normalized statement of the signal)
- `claim_vector` (embedding)
- `confidence_band` (one of: hard-datum, reported-claim, inference, weak-inference)
- `proposed_anchors` (list of suggested graph operations: create node, update node, create edge, update edge, with reasoning)
- `extracted_at` (timestamp)

The schema for `proposed_anchors` is defined in `augur-graph-schema.md`. The pipeline-level details of how lenses produce these are in `augur-signal-pipeline.md`.

### Tier A → Anchoring

**Trigger:** anchoring runs on batches of recent Tier A signals, typically on a schedule (hourly or every few hours), not on every signal individually. Batching gives the anchoring stage the cross-signal context it needs to detect convergence and avoid creating redundant graph changes.

**Input to anchoring:**
- A batch of recent signal records.
- A snapshot of the relevant subgraph (nodes and edges referenced by `proposed_anchors`, plus their neighborhoods).

**Output of anchoring:** a list of **graph update events**, each with:
- `event_id` (UUID)
- `event_type` (create_node, update_node, create_edge, update_edge_weight, deprecate_edge, etc.)
- `target` (node ID, edge ID)
- `change` (the actual delta — e.g., weight delta, new metadata, etc.)
- `triggered_by` (list of signal_ids)
- `reasoning` (free-text explanation)
- `confidence` (qualitative)
- `applied_at` (timestamp; null until applied)

### Anchoring → Tier B

The anchoring layer **proposes** events. A deterministic applier consumes events and writes to the graph. The applier enforces invariants (weight bounds, schema validity, no orphan edges) and rejects malformed events. Rejected events are logged but not applied.

This separation is critical. The LLM never directly mutates the graph. The applier is the only thing that writes to Tier B, and its logic is plain code that can be tested and reasoned about.

### Tier B → Read paths

Three read paths consume the graph:
- **Disconfirmation:** identifies high-weight edges due for challenge. Schedules its own LLM passes.
- **Projection:** walks the graph forward from a query node or condition, producing conditional branch outputs.
- **UI / API:** serves graph state to the human user.

Each is read-only against Tier B. They never write directly; if they need to record outputs (e.g., disconfirmation results), they emit new graph update events through the same applier the anchoring layer uses.

---

## The disconfirmation loop

Confirmation bias is the default failure mode of any system like Augur. The disconfirmation loop is the architectural countermeasure.

**Cadence:** weekly to begin with. Adjustable.

**Behavior:**
1. Select the top-N edges by weight that haven't been challenged in the last cycle.
2. For each, prompt a strong model: *"Given this edge claims X → Y with weight W, what evidence from the last [cycle period] would weaken this claim? Cite specific signals."*
3. Output is a structured response: either "no significant disconfirmation found" with reasoning, or proposed weight reductions with citations.
4. The output goes through the same anchoring → applier flow as any other update.

This loop is what prevents the graph from accumulating drift over months. Architecturally it is symmetric with the main ingestion loop, just with a different prompt and a different selection criterion.

---

## Projection — what it is and isn't, structurally

Projection is **not** a separate model or system. It's a structured walk on the graph driven by an LLM prompt that:

1. Takes a current graph state (or a query-restricted subgraph).
2. Identifies activated nodes (conditions currently true based on recent signals).
3. Walks outward through edges, multiplying conditional weights to produce candidate trajectories.
4. Returns a branching structure: *"if A persists, then B is more likely (weight 0.6); if A and C, then D becomes plausible (weight 0.4); the most fragile assumption in this branch is E."*

The architecture treats projection as a read-only consumer of the graph. It doesn't write back. If users want to record a particular projection as a saved scenario, that's a separate Tier B record (a "scenario snapshot") referencing the graph state and the walk parameters at the time.

The interesting design property: because projection is cheap to re-run and the graph updates continuously, projections aren't durable forecasts. They are momentary readings of the graph, like a thermometer. Yesterday's projection is interesting historically but is not the answer for today.

---

## Observability and operator ergonomics

A single-operator system needs strong introspection. The architecture commits to:

- **`llm_calls` is the LLM observability backbone.** Every LLM call from any stage is recorded in the `llm_calls` table with the stage, prompt template ID, model, the caller's entity IDs (payload, signal, lens, edge, ...), the full input messages, the output, tokens, cost as reported by OpenRouter, latency, attempts and outcome. The row is written *before* the call is made: if it cannot be written, the call is not made, so there are no unrecorded calls.
- **Analysis is SQL next to the graph.** Because calls live in the same database as signals and graph update events, questions like *"show me all extraction calls from Indian sources last week that produced zero signals"* or *"which prompt template produces the most rejected anchors"* are joins, not exports. `augur llm-calls` lists and summarises calls (cost, tokens, p50/p95 latency and failures by stage, template and model); `augur llm-call <id>` shows one in full.
- **Budget enforcement reads the same table.** The daily cap is the sum of today's reported costs for budgeted calls, so it holds across the API server, the scheduler and the CLI, and survives restarts. A budgeted call whose cost OpenRouter does not report is an error: an unmetered call is a budget leak.
- **Every graph update event is auditable** to its triggering signals and from there to its source payloads, and to the LLM calls involved: nodes and edges carry `llm_call_ids`, and disconfirmation pass events carry `llm_call_id`.
- **OpenRouter Broadcast is optional and additive.** Every request carries OpenRouter's `trace` field with `trace_id` set to the `llm_calls.call_id`, the stage and the prompt template ID. If Broadcast is switched on (OpenRouter dashboard), its traces can be sent to any OpenTelemetry-compatible tool and still join back to Augur's records.
- **A small operator UI or CLI** for the operator to inspect recent extractions, recent anchoring events, recent disconfirmation results, and recent cost summaries. This is not the user-facing graph UI; it is the maintenance interface.
- **A "why does Augur think this?" trace** available for any node or edge: list of supporting signals, list of source payloads, list of disconfirmation passes that examined it, current weight and weight history, and the LLM calls behind it.

The architectural commitment is that every LLM call is recorded where the graph it shaped can be joined to it. Retention of `llm_calls` (the messages column dominates its size) is an operator decision; graph records keep their `llm_call_ids` even if old call rows are pruned.

---

## Sibling systems

Augur Discourse (`mlossius-dev/augur-discourse`) is a sibling system: it measures what is being talked about across platforms and sets that against what Core models as happening. It runs on the same VPS and the same PostgreSQL instance, in its own `discourse` schema, as its own database role. Its design is in that repository's `docs/discourse-vision.md`.

The relationship is a **one-way, read-only bridge**:

- Core publishes a schema, `core_bridge`, that contains **views only** (migration `010_core_bridge.sql`). The views are a published contract: Discourse reads them, and only them.
- The `discourse` role has `USAGE` on `core_bridge` and `SELECT` on its views. It has no grant on any other Core object. The views run with their owner's rights, so Discourse never needs, and never gets, access to the tables behind them.
- **Core never reads Discourse.** Nothing Discourse produces enters the signal pipeline, Tier A, or the graph. The social-media exclusion in `augur-sources.md` is unchanged: Discourse's material is exactly the non-independent echo that Tier A's convergence detection must never see.

The views:

| View | Contents | Time column for `as_of` |
|---|---|---|
| `aliases` | `alias_text`, `canonical_name`, `canonical_node_id` | `added_at` |
| `topics` | `topic_id`, `name`, `description`, `dimension` | `created_at` |
| `topic_nodes` | `topic_id`, `node_id`, `node_type`, `node_name` | `added_at` |
| `events` | event nodes: `node_id`, `name`, `occurred_at`, `event_kind`, `subject_entities`, `occurred_location` | `occurred_at` |
| `condition_history` | condition state transitions with node name | `content_timestamp` |
| `quantity_series` | market and macro series normalized to `series_key`, `label`, `unit`, `ts`, `value` (from FRED and Yahoo payload metadata) | `ts` |
| `edges_summary` | live edges: source, target, names, `edge_type`, `current_weight_band` | `created_at` |
| `edge_weight_history` | weight-band changes per edge | `content_timestamp` |
| `payload_index` | news payload metadata only: source, perspective, language, title, url | `content_timestamp` |

Every view exposes the time column Discourse needs to read it `as_of` a past moment, consistent with replay mode below.

Note on placement: Core's migration runner sets `search_path = ag_catalog, "$user", public` (needed for AGE), so Core's own tables are created in the `ag_catalog` schema rather than `public`. The bridge views name tables unqualified and therefore bind to them wherever they are. Moving Core's tables out of `ag_catalog` is a separate decision; the views would need re-creating if that happens.

**Contract discipline.** Adding a column to a view is backward-compatible. Renaming or removing a column, changing its type, or changing what a view means is a breaking change: it needs a matching change in Discourse's bridge reader, whose contract tests fail loudly on any drift. The migration is idempotent (`CREATE OR REPLACE VIEW`), because Core's migration runner re-applies migrations that do not record themselves.

#### Design decision: views in the same instance, not an API

The alternative considered was a small read-only HTTP API in front of Core for Discourse to call.

**Current decision: views in a dedicated schema.** No new service, no network failure modes, and `as_of` filtering is plain SQL. The permission boundary is enforced by PostgreSQL grants and tested from the Discourse side.

**Revisit trigger:** Discourse load measurably degrades Core (query latency, disk, vacuum pressure). Then Discourse moves to its own database, and these views become the specification for a read-only API.

---

## What the architecture deliberately does not include

To keep the system buildable by one person, the architecture excludes:

- **No multi-tenant support.** One operator, one graph. Future-Morten or collaborators read the same graph as Morten.
- **No real-time user-facing alerts.** Augur is for reflection, not notification. A user who wants alerts is using the wrong tool.
- **No native mobile client.** Web only.
- **No fine-grained authorization.** Either you have access to the graph or you don't.
- **No automatic action.** Augur never executes anything in response to its own conclusions. The output is information, not decisions.
- **No paid data feeds at the architecture layer.** Sources that require paid APIs (Bloomberg, Kpler, S&P Capital IQ) can be added later as sources, but the architecture cannot assume they are present.

These are scope limits, not philosophical limits. Most could be added later if the project warrants. They are excluded now because each one would multiply complexity in ways that prevent the core system from being completed.

---

## Migration and revision posture

This architecture commits to specific choices (Postgres + AGE, FastAPI, OpenRouter, Docker on a VPS). It does not commit to those choices forever.

Specifically, the following are expected to be revisited:

- **Graph backend** — AGE is the right starting point. Neo4j or a dedicated graph DB becomes worth considering if traversal performance on large subgraphs becomes a bottleneck.
- **Vector backend** — pgvector is sufficient for ~10M claim vectors. If Tier A grows beyond that despite the 90-day TTL, Qdrant or similar becomes worth considering.
- **Orchestration** — APScheduler → Dramatiq is the expected near-term migration. Beyond that, Temporal or similar becomes relevant only if workflow complexity exceeds what task queues handle well.
- **Hosting** — single-VPS is the right starting point. Splitting components across hosts is a later optimization, not a near-term concern.

What is not expected to change:

- The two-tier memory model.
- The separation of extraction from anchoring.
- The "LLM proposes, applier disposes" pattern for graph mutations.
- The disconfirmation loop as a first-class subsystem.
- The principle that all LLM inference is external and swappable.
- **Replay-mode support as a first-class capability** (see below).

These are architectural commitments. The rest are implementation choices.

---

## Replay mode

A first-class architectural requirement, not a debugging feature.

Augur must support setting an effective system time — a cutoff — such that all reads see only signals, events, payloads, and graph states that existed at or before that time. This enables retroactive seeding, source-confidence calibration, and the operator's ability to ask *"what did Augur look like in March?"* with a real answer rather than a guess.

The implications for the rest of the architecture:

- **Every signal, payload, event, and graph update carries a hard timestamp at the moment it represents, not at the moment it was processed.** When a historical article is ingested in 2026 but dated 2024, the signal it produces is timestamped 2024. The applier records the graph update event at the 2024 timestamp, not at processing time. This is non-negotiable; without it, replay mode is corrupted by ingestion ordering.
- **Tier B graph state is reconstructible at any historical timestamp** by replaying the weight_history and node activation_history up to that point. The schema (see `augur-graph-schema.md`) already commits to append-only history for this reason.
- **Tier A is not historically replayable** beyond its 90-day TTL. Signals that aged out are gone from working memory. This is acceptable because anything that mattered was already promoted to Tier B by anchoring.
- **LLM calls during replay are sandboxed.** Extraction prompts during retroactive runs are constructed to instruct the model to reason from the historical date only, and where possible, models are selected whose training cutoff predates the replay window. The orchestration layer's model-selection capability supports this directly.
- **The operator UI and any future read API must accept an `as_of` parameter** that scopes all queries to a historical timestamp. The default is "now," but the capability is always present.

### Historical backfill (operator decision, October 2026)

Retroactive seeding of the live graph, as a bounded first step before any full calibration run. Decision, scope and constraints:

- **Scope.** 2026 only, at most 25 payloads per day (about 7,200 in all), headline and lead only, the same format as the live RSS sources. Widening it (2025, more per day) waits until this run shows the graph improves.
- **Source.** Archived snapshots of feeds already in the source registry, from the Internet Archive's Wayback Machine (see `augur-sources.md`, "Archive access"). No new sources are admitted for it. Coverage in 2026 is uneven: Al Jazeera nearly daily, TASS, Bloomberg, FT and Xinhua on some days; a day's 25 are drawn with a per-source cap so one feed does not dominate.
- **Order.** Newest first: from the earliest live data backwards, one day at a time. This is a deliberate exception to the chronological order replay otherwise uses, chosen so each processed day adds immediately usable history. It is safe because the Applier never lets an older operation override a newer state (`augur-graph-schema.md`, "Time order versus processing order"). The cost, accepted by the operator: anchoring a March signal sees the October graph, so node choice can be informed by later events. Extraction is sandboxed to the payload's date as in calibration replay, with the date instruction in the user message so the lens system prompts stay cacheable.
- **Models and cost.** Extraction and anchoring run on a paid low-cost model (Ling 3.0 Flash at the time of writing: about $0.0007 per payload, so about $5 for the run) because the free tier has no spare capacity. Paid calls do not count against the free-tier call quota, which only free-model calls consume; the backfill has its own spend cap (`BACKFILL_BUDGET_USD`, default 4) on top of the daily budget, and stops when either is reached.
- **Provenance.** Every signal records the model that extracted it and whether it came from live ingestion or the backfill, so backfilled material can be weighted, compared or removed later. Every call is recorded in `llm_calls` as usual.
- **Revisit trigger:** if the backfilled history visibly improves trends and reasoning, widen scope; if it mostly adds noise, stop and consider the dedicated calibration run instead (`augur-calibration.md`).

The architectural cost of replay mode is modest if it's designed in from the start. Adding it later would require backfilling timestamps and rebuilding the history mechanisms, which is significantly harder.
