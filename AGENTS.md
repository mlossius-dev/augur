# AGENTS.md

Operating instructions for LLM agents working on this codebase. Read this in addition to `README.md` and the documents in `docs/`.

---

## What Augur is, in one paragraph

Augur is a reasoning prosthetic that ingests world signals (news, central bank data, shipping, seismic, aircraft, mining, commodities), extracts structured claims through multi-lens LLM extraction, anchors those claims into a navigable causal graph, periodically challenges its own beliefs through a disconfirmation pass, and presents the graph as a tool for humans to think with. The graph is the product. It does not predict the future; it makes the present and its plausible futures legible.

---

## Read the documents before writing code

This is the most important instruction in this file. The eight documents in `docs/` are the source of truth for every architectural decision. Before writing code for a phase or component, read the relevant documents. Specifically:

- Working on **infrastructure or storage**? Read `docs/augur-architecture.md` first.
- Working on **the graph layer, schema, or applier**? Read `docs/augur-graph-schema.md` first.
- Working on **ingestion, extraction, or anchoring**? Read `docs/augur-signal-pipeline.md` first.
- Working on **source integration or the source registry**? Read `docs/augur-sources.md` first.
- Working on **replay mode or calibration**? Read `docs/augur-calibration.md` and the relevant section of `docs/augur-architecture.md`.
- Working on **the user interface or conversation layer**? Read `docs/augur-presentation.md` first.
- Unsure where you are in the build sequence? Read `docs/augur-roadmap.md`.

The documents cross-reference each other. Follow the cross-references when something feels unclear; don't guess.

---

## The current phase

Check `docs/augur-roadmap.md` to see which phase the project is in. Work only on tasks within the current phase unless explicitly told otherwise. The phases are designed to be sequenced; building Phase 8 work during Phase 2 (or worse, mixing them in the same PR) is the single most common failure mode this project guards against.

If a task seems to belong to a later phase than the current one, surface this to the operator before proceeding. Either the task is misclassified, the phase boundary needs to flex, or the work should wait.

---

## Hard architectural commitments

These are not flexible. Do not propose changing them without an explicit conversation with the operator first.

- **No LLM ever writes directly to the graph.** The applier is plain Python code. LLMs propose structured anchors; the applier validates and applies them. This is not a code-organization preference; it's a safety architecture.
- **All LLM inference is external via OpenRouter.** The VPS never runs models locally. Do not introduce local-inference dependencies.
- **Every LLM call emits a Langfuse trace.** No silent calls. The trace must include the prompt template ID, model, stage tag, and any relevant signal/event IDs.
- **Replay mode is first-class.** Every signal, event, and graph update carries a `content_timestamp` that is the time the content represents, not the processing time. Do not introduce code that uses `now()` where it should use `content_timestamp`.
- **The graph schema is fixed.** Six node types, nine edge types, five weight bands plus `disputed`. Adding any of these requires a schema revision conversation, not a code change.
- **The `falsification_criteria` field is required on every edge.** The applier must reject edges without it.
- **One PostgreSQL database for everything.** With pgvector, pg_trgm, PostGIS, and Apache AGE extensions. Do not introduce Neo4j, Pinecone, Qdrant, or other databases without explicit revision of `docs/augur-architecture.md`.

If a piece of code you're writing would violate any of these, stop and surface the issue.

---

## Style and conventions

### Code style

- **Python 3.12+.** Use modern Python features (`match`, structural type hints, etc.) where they make code clearer.
- **Type hints everywhere.** Function signatures, dataclass fields, return types. The pipeline has many structured data shapes; types are the first line of defense against drift.
- **Pydantic for structured data crossing layer boundaries.** Payloads, Signals, ProposedAnchors, GraphUpdateEvents — all Pydantic models. Plain dataclasses for internal-only structures are fine.
- **Async by default in I/O paths.** Ingestion, LLM calls, and database access should be async. CPU-bound work can stay sync.
- **One module per pipeline stage.** Ingestion, extraction, Tier A, anchoring, applier, Tier B read paths, disconfirmation each live in their own package. Do not collapse them.

### Naming

- Use the vocabulary from the documents exactly. A signal is a `Signal`, not a `Claim` or `Observation`. An edge is an `Edge`, not a `Relationship`. The applier is the `Applier`, not the `Writer` or `Updater`. The documents define the vocabulary; code matches it.
- Use snake_case for Python, camelCase only where matching external APIs (Langfuse, OpenRouter, etc.).
- Module-level constants in SCREAMING_SNAKE_CASE.
- Database table names match the document vocabulary: `signals`, `payloads`, `nodes`, `edges`, `edge_weight_history`, `graph_update_events`, etc.

### Logging and observability

- **Structured logging only.** Use `structlog` or equivalent. Every log line should have a stage tag and the relevant entity IDs (payload_id, signal_id, event_id, etc.).
- **Langfuse traces for every LLM call.** Never call OpenRouter directly; go through the LLM client abstraction. The abstraction emits the trace.
- **Cost tracking at the call site for budget enforcement only.** Analytical cost dashboards live in Langfuse, not in application code or tables.

### Testing

- **Unit tests for the applier are mandatory and must be exhaustive.** The applier is the gate to the graph. Every invariant it enforces (schema validation, alias resolution, weight bounds, falsification_criteria presence) needs a test. The applier failing silently is the worst failure mode in this system.
- **Integration tests for each pipeline stage's boundary contract.** Ingestion produces Payloads matching the schema; Extraction produces Signals matching the schema; Anchoring produces ProposedAnchors matching the schema. Tests verify each contract.
- **No mocked LLM calls in tests.** Either use real LLM calls against cheap models (with caching), or use recorded fixtures of past responses. Mocking LLM outputs is how you ship code that works against a fictional model.

---

## When to ask vs. when to proceed

This is one of the most important judgment calls for an agent on this project.

**Proceed without asking when:**

- The task is clearly within the current phase per `docs/augur-roadmap.md`.
- The documents specify how it should be built.
- Your work doesn't violate any hard architectural commitments above.
- You're following existing patterns in the codebase.
- You're writing tests, fixing bugs identified by tests, or making refactors that don't change behavior.

**Ask before proceeding when:**

- The task seems to belong to a later phase than the current one.
- The documents are silent or ambiguous on how something should be built.
- You're tempted to violate a hard architectural commitment.
- You're tempted to add a new dependency (library, service, database).
- You're tempted to change the graph schema (node types, edge types, weight bands).
- You're tempted to change a document — documents change *before* the code that motivates the change, not after.
- You're tempted to add a new node type, edge type, or perspective pool.
- You're considering adding social media as a source (the exclusion is documented and intentional).
- You're considering adding user preference settings to the presentation layer (also documented and intentional).
- The task description doesn't match what the documents describe.

**Always ask, never assume:**

- The current calibration weights or source-registry contents. These live in data, not documents. If you need them, query the database or load the relevant config.
- The operator's intent when a task description is short or ambiguous. A 30-second clarification saves hours of misdirected work.

---

## Documents-first discipline

When implementation reveals that a document is wrong, the document is updated *before* the code that implements the new understanding is committed.

This is a discipline, not bureaucracy. The reason:

- The documents are the design substrate. Code without updated documents drifts silently.
- Future agents (including you, weeks later) will read the documents and trust them. Code that contradicts the documents creates ghost decisions that nobody remembers making.
- The decision/alternative/revisit-trigger blocks throughout the documents are themselves valuable artifacts. Bypassing them and "just changing the code" destroys the reasoning trail.

If a document update is needed, surface it. Either you make the update as part of the same change (if the operator agrees), or you pause the code work until the document is updated.

---

## Common failure modes to avoid

These are documented in `docs/augur-roadmap.md` "Failure modes to watch for." The most relevant ones for code work:

- **Building out of phase order.** Especially tempting around the presentation layer. If you're writing UI code during Phase 2, something is wrong.
- **Letting the schema sprawl.** Resist adding node types, edge types, or weight bands. Most apparent needs are solved by being more careful with the existing types.
- **Letting the source registry sprawl.** Don't add sources speculatively; the operator admits them deliberately.
- **Treating calibration as optional.** Phase 6 is a hard gate. Live operation begins after calibration, not before.
- **Skipping tests on the applier.** See above. The applier is the gate.
- **Mocking LLM outputs.** Use real cheap models or recorded fixtures.

---

## Completed phases and their key components

### Phase 8 — Presentation layer (dimensions, changes, reasoning)

- **`src/augur/presentation/dimensions.py`** — `compute_dimension_scores()` aggregates condition/edge counts per dimension into a `DimensionScore` with a `state` band (nominal/elevated/critical).
- **`src/augur/presentation/changes.py`** — `get_recent_changes()` pulls the most significant graph updates in a time window.
- **`src/augur/presentation/reasoning.py`** — `build_reasoning_chain()` traces the causal path between two nodes.
- **`src/augur/api/home.py`** — `GET /api/home` returns dimension scores + recent changes in one call.
- **`src/augur/api/reasoning.py`** — `GET /api/reasoning?from_node=&to_node=` returns the causal chain.
- **`static/js/home.js`**, **`static/js/reasoning.js`** — client-side rendering for the home view and reasoning drill-down.
- **`static/css/augur.css`** — stylesheet for all views.

### Phase 9 — Topics and geographic scoping

- **`src/augur/db/migrations/005_topics.sql`** — `topics`, `topic_nodes`, `region_scope_definitions` tables; 8 seeded regions.
- **`src/augur/presentation/topics.py`** — `get_topic_list()`, `get_topic_detail()`, `create_topic()`, `assign_nodes_to_topic()`. Topic state is derived from the active/total condition ratio, not stored explicitly.
- **`src/augur/presentation/geo.py`** — `infer_region(lat, lon, region_definitions)` picks the smallest bounding box that contains the point (area-sorted candidates list). `get_regional_scope()` filters dimension scores and changes for the inferred region.
- **`src/augur/api/topics.py`** — `GET /api/topics`, `GET /api/topics/{topic_id}`.
- **`src/augur/api/geo.py`** — `GET /api/geo/scope?lat=&lon=&as_of=`.
- **`src/augur/cli/operator.py`** — `augur topics create/list/assign/nodes` subcommands.
- **`static/js/topic.js`** — topic list and detail rendering; `loadGeoScope()`.

### Phase 10 — Scenario projection

- **`src/augur/db/migrations/006_scenarios.sql`** — `scenarios` table with `probability_band CHECK` constraint.
- **`src/augur/projection/`** package:
  - `models.py` — `ProbabilityBand(StrEnum)`: HIGH/MODERATE/LOW/NEGLIGIBLE; `Scenario`, `GraphEvidence`, `ProjectionResult`.
  - `evidence.py` — `gather_evidence()` pulls capped graph evidence (conditions, edges, changes) filtered by dimension keywords.
  - `prompts.py` — system prompt + `build_user_message()` formats evidence for the LLM.
  - `parser.py` — `parse_scenarios()` strips markdown fences, validates JSON, defaults unknown probability bands, skips malformed items.
  - `store.py` — `save_scenarios()` with optional previous-deprecation; `get_scenarios()`.
  - `orchestrator.py` — `ProjectionOrchestrator.run_projection()` / `run_all_dimensions()`; uses `PipelineStage.PROJECTION`.
- **`src/augur/api/scenarios.py`** — `GET /api/scenarios?dimension=&as_of=&limit=`, `GET /api/scenarios/{scenario_id}`.
- **`src/augur/scheduler.py`** — Sunday 06:00 UTC `scenario_projection` CronTrigger job.
- **`static/js/scenarios.js`** — `loadScenarios()`, `renderScenarioCard()`.

### Phase 11 — Conversation layer

- **`src/augur/db/migrations/007_conversation.sql`** — `conversation_sessions` and `conversation_messages` tables; `prune_old_sessions()` PL/pgSQL function.
- **`src/augur/conversation/`** package:
  - `context.py` — `retrieve_context()` uses pg_trgm `similarity()` (threshold 0.1) to match nodes; pulls connected edges, recent signals (ILIKE on node name terms), dimension summaries. No vector embeddings — deliberately simple.
  - `prompts.py` — `build_messages()` injects context first, then interleaves session history (last 6 turns), then the current question.
  - `session.py` — `create_session()`, `touch_session()`, `get_session_history()`, `save_message()`, `prune_sessions()`.
  - `orchestrator.py` — `ConversationOrchestrator.ask()` wires retrieval → history → LLM → persistence; uses `PipelineStage.CONVERSATION` (free-tier Gemini Flash key).
- **`src/augur/api/conversation.py`** — `POST /api/conversation/query`, `GET /api/conversation/{session_id}`.
- **`src/augur/scheduler.py`** — Sunday 07:00 UTC `prune_sessions` CronTrigger job.
- **`static/js/conversation.js`** — `submitQuestion()`, `appendMessage()`, `clearConversation()`; multi-turn session state held client-side.

### Phase 12 — Production hardening

- **`src/augur/middleware/auth.py`** — `APIKeyMiddleware`: gates all `/api/*` paths behind `X-API-Key` header when `AUGUR_API_KEY` env var is set. Open-access dev mode when the var is unset.
- **`src/augur/middleware/ratelimit.py`** — `ConversationRateLimitMiddleware`: sliding-window (deque of timestamps per IP) rate limiter applied only to `POST /api/conversation/query`. Returns 429 with `Retry-After` header when the IP exceeds `CONV_RATE_LIMIT_PER_MINUTE` (default 10) requests per 60 s. IP extracted from `X-Forwarded-For` (first entry) or `request.client.host`.
- **`src/augur/config.py`** — Added `augur_api_key: str | None = None` and `conv_rate_limit_per_minute: int = 10` to `Settings`.
- **`src/augur/main.py`** — Both middleware wired into `create_app()`; rate limiter added before auth so the IP bucket is checked first (Starlette middleware stack is LIFO, so add rate limiter first to have it execute first).

---

## Tools, infrastructure, and access

- **Docker Compose** orchestrates the local and VPS environments.
- **Postgres 16+** with pgvector, pg_trgm, PostGIS, and Apache AGE extensions.
- **SearXNG** for general web search (existing instance on the VPS).
- **Langfuse** for LLM observability (existing instance on the VPS).
- **OpenRouter** for all LLM inference. Two API keys: one for production-quality models on the main pipeline, one free-tier-only for the conversation layer.
- **Object storage** (Hetzner or Backblaze B2) for backups.

The local development environment should mirror the VPS structure as closely as possible. Docker Compose makes this straightforward; use it.

---

## Cost discipline

LLM inference costs money. Be aware of:

- **Lens extraction is the highest-volume LLM stage.** Use cheap models (small fast OpenRouter offerings) by default. Reserve stronger models for anchoring and disconfirmation.
- **The conversation layer uses a separate free-tier-only key.** Do not route conversation calls through the main key.
- **Daily budget caps are enforced at the LLM client layer.** If a budget is hit, the client refuses further calls until the next cycle. Do not work around this.
- **Calibration runs are expensive.** A single replay run can process tens of thousands of payloads. Use the cheapest viable model class. Cache aggressively.

If you find yourself writing code that would cause a spike in LLM calls, surface it.

---

## Working with the operator

The operator is one person, working part-time, with limited but real attention to give to the project. To work efficiently together:

- **Surface decisions early.** A 30-second message asking "should this be X or Y" saves hours of work in the wrong direction. The operator would rather be interrupted than re-do work.
- **Batch questions.** If you have three questions, ask all three in one message rather than one at a time.
- **Reference documents and line numbers when discussing design.** "In `docs/augur-graph-schema.md` under 'Weight semantics' it says..." is much more useful than "the docs say..."
- **Be honest about uncertainty.** If you're 60% sure of an approach, say so. The operator can clarify or accept the risk. Pretending to be 100% sure is how bad decisions get made.
- **Don't apologize at length when something is wrong.** Just identify what's wrong, propose the fix, and move on.

---

## A final principle

The project's vision document says: *"Augur is not trying to be right about the future. It is trying to make the present and its plausible futures more legible."*

The same principle applies to working on the codebase. The goal is not to write impressive code, ship features fast, or demonstrate cleverness. The goal is to make the system more legible — to the operator, to future agents, and to the user.

Code that is correct, well-documented, well-tested, and understandable beats code that is fast, clever, or extensive. When in doubt, choose legibility.
