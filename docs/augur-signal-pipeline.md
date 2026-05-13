# Augur — Signal Pipeline

*Stage-by-stage detail on how raw inputs become graph updates. Inherits from `augur-vision.md`, `augur-architecture.md`, and `augur-graph-schema.md`. Replay mode, source confidence calibration, and retroactive seeding methodology live in `augur-calibration.md`.*

---

## Reading guide

This document is implementation-ready. It tells an engineer (or an LLM coder) what each stage of the pipeline must do, what it consumes, what it produces, and what invariants it must preserve.

Cross-references:
- Component boundaries and data contracts → `augur-architecture.md`
- Node, edge, and proposed_anchors schemas → `augur-graph-schema.md`
- Source registry and tiering → `augur-sources.md`
- Calibration methodology → `augur-calibration.md`

---

## The pipeline at a glance

```
   ┌──────────────┐
   │   SOURCES    │   News, APIs, structured feeds
   └──────┬───────┘
          │
          ▼
   ┌──────────────┐
   │  INGESTION   │   Fetch, normalize, archive raw payloads
   └──────┬───────┘
          │
          ▼
   ┌──────────────────────────────────┐
   │  MULTI-LENS EXTRACTION            │
   │  Same payload, N parallel lenses  │
   │  Each emits zero or more signals  │
   └──────┬───────────────────────────┘
          │
          ▼
   ┌──────────────────────────────────┐
   │  TIER A: RAW SIGNAL STORE        │
   │  Dedupe, cluster, detect          │
   │  convergence across perspectives  │
   └──────┬───────────────────────────┘
          │
          ▼
   ┌──────────────────────────────────┐
   │  ANCHORING                       │
   │  Batched LLM pass: signals +     │
   │  relevant subgraph → proposed    │
   │  graph update events             │
   └──────┬───────────────────────────┘
          │
          ▼
   ┌──────────────────────────────────┐
   │  APPLIER (deterministic)         │
   │  Validate, normalize, apply      │
   │  events to Tier B graph state    │
   └──────┬───────────────────────────┘
          │
          ▼
   ┌──────────────────────────────────┐
   │  TIER B: GRAPH STATE             │
   └──────────────────────────────────┘

   (Separate cycle, periodic)
   ┌──────────────────────────────────┐
   │  DISCONFIRMATION PASS            │
   │  Reads high-weight edges, asks   │
   │  "what would weaken this?"       │
   │  Emits update events through     │
   │  the same applier                │
   └──────────────────────────────────┘
```

---

## Stage 1: Ingestion

The job of ingestion is to get raw external content into a normalized form that downstream stages can consume without knowing where it came from.

### Sub-stages

**Fetch.** Pull content from a source according to its access pattern:
- HTTP scraping via Playwright (for sites that need JS rendering).
- Direct HTTP for simple endpoints.
- SearXNG-mediated search for general queries.
- Structured API calls for sources like USGS, FRED, IMF, WGC, ADS-B Exchange, AIS providers.
- RSS for syndicated feeds.
- File-based ingestion for offline archives (PDFs, CSVs).

**Normalize.** Convert each fetched item into the canonical `Payload` shape:

```yaml
payload_id: <UUID>
source_id: <FK to source registry>
fetched_at: <UTC timestamp; when Augur fetched it>
content_timestamp: <UTC timestamp; when the content represents — publication date for news, observation date for API data>
perspective: <one of: us_eu, india, china, gulf, russia, nordic, ...>
content: <text body, structured data, or canonical representation>
content_type: <article | api_response | structured_feed_entry | filing | dataset_row>
language: <ISO code>
metadata:
  url: <if applicable>
  headline: <if applicable>
  source_native_id: <e.g., RSS GUID, article ID>
  raw_response: <opaque bytes/JSON for audit>
```

The distinction between `fetched_at` and `content_timestamp` is the foundation of replay mode (see `augur-architecture.md`). Downstream stages always use `content_timestamp` for signal time anchoring; `fetched_at` is only for operational debugging.

**Archive.** Store the raw payload to filesystem and replicate to object storage as backup. Payloads are write-once and indefinitely retained. Their cost is small compared to their auditability value.

**De-spam.** Apply lightweight heuristics to reject obvious junk before paying for LLM extraction:
- Articles below a length threshold.
- Pure SEO content (signal-to-noise heuristics on link density, ad density, formulaic phrasing).
- Exact duplicates of already-ingested payloads from the same source within a short window.

Rejected payloads are still archived with a `rejected_reason` flag — useful for tuning the de-spam logic later.

### What ingestion does not do

- It does not interpret content.
- It does not assign confidence.
- It does not identify topics or signals.
- It does not deduplicate across sources (that's Tier A's job).

Ingestion's job is to faithfully capture and normalize. Everything else is downstream.

---

## Stage 2: Multi-lens extraction

The core insight from the TradingAgents architecture, retargeted for Augur: **the same payload is read multiple times by different LLM lenses, each with different priors and looking for different kinds of signals.**

### What a lens is

A lens is a fixed configuration consisting of:

- A `lens_id` (slug).
- A `system_prompt` defining what the lens cares about and what to ignore.
- A `signal_schema` describing what shape of output it produces (always a list of signals matching the structure defined in `augur-graph-schema.md`'s proposed_anchors).
- A `graph_scope` declaring which node types and edge types this lens is allowed to propose. (A commodities lens cannot propose `Scenario` nodes. A demographics lens cannot propose `produces` edges.)
- A `model_class` indicating which OpenRouter model tier to use (cheap / mid / strong).
- An optional `language_competence` list restricting the lens to certain content languages.

Lenses are versioned. A change to a lens prompt creates a new lens version; existing signals reference the lens version that produced them.

### The starting lens catalog

These are the lenses defined for the initial system. They are not exhaustive and will evolve as calibration runs reveal which lenses produce durable signal.

#### `commodities`
Reads any payload for signals about physical commodity flows, production, prices, and supply chain disruption.
- Looks for: production volumes, export/import data, price moves, infrastructure outages, weather impacts on agriculture, shipping disruptions.
- Ignores: political narrative, financial market commentary except where directly tied to commodity flows.
- Graph scope: `Entity`, `Condition`, `Event`, `Quantity`, plus `causes`, `constrains`, `produces`, `part_of`.

#### `financial`
Reads for signals about capital flows, monetary policy, banking, currency, and asset markets.
- Looks for: central bank actions and statements, reserve composition changes, currency moves, debt issuance, banking sector stress, large institutional flows.
- Ignores: commodity-specific information except where it ties to financial system stress.
- Graph scope: `Entity`, `Condition`, `Event`, `Quantity`, `Claim`, plus all causal and relational edge types.

#### `geopolitical`
Reads for signals about state-to-state relations, alliances, conflict, treaties, sanctions, and diplomatic activity.
- Looks for: announcements, statements, military movements, sanctions imposed or lifted, treaty signings or violations, leadership changes.
- Ignores: economic detail beyond direct geopolitical impact, sectoral commentary.
- Graph scope: `Entity`, `Condition`, `Event`, `Claim`, plus causal and relational edges.

#### `physical_world`
Reads structured data sources (seismic feeds, AIS data, ADS-B data, satellite-derived indicators, weather extremes) for signals derived from physical observation.
- Looks for: anomalies, threshold crossings, pattern changes, large discrete events.
- Ignores: narrative; this lens operates almost entirely on structured numeric input.
- Graph scope: `Event`, `Quantity`, plus `causes` and `correlates_with` edges.

#### `regulatory`
Reads for signals about legal, regulatory, and policy changes that affect markets, technology, trade, or rights.
- Looks for: new laws, rule changes, enforcement actions, court rulings, regulatory agency statements.
- Ignores: implementation detail beyond what affects the regulated sector.
- Graph scope: `Entity`, `Condition`, `Event`, plus causal edges.

#### `narrative_divergence`
A meta-lens. Reads how different perspectives are framing the same underlying event or condition.
- Looks for: same event reported with materially different emphasis, vocabulary, or framing across perspectives (US/EU vs India vs China vs Russia vs Gulf).
- Ignores: events reported similarly across perspectives.
- Produces: `Claim` nodes (claims about how a thing is being framed) and `contradicts` edges between competing framings.
- Graph scope: `Claim` and `contradicts` edges primarily.

#### `disconfirmation`
A bear-case lens. Reads each payload looking specifically for evidence that contradicts existing high-weight edges in the graph.
- Looks for: anything that, if true, would weaken a currently-active condition or causal edge in the graph.
- Ignores: corroboration of existing edges (that's other lenses' job).
- Produces: `add_disconfirming_signal` operations on existing edges, plus occasional `update_edge_weight` proposals with `direction: weaken`.
- Graph scope: existing edges only. Cannot create new nodes or new edges.
- Note: this lens runs primarily during the disconfirmation pass (stage 6), but a lightweight version can also run inline during normal extraction.

### What lenses produce

Each lens, for each payload, produces zero or more `Signal` records. The structure:

```yaml
signal_id: <UUID>
payload_id: <FK>
lens_id: <which lens>
lens_version: <version>
extracted_at: <UTC; processing time>
content_timestamp: <inherited from payload>
claim_text: <concise normalized statement>
claim_vector: <embedding of claim_text>
confidence_band: <hard_datum | reported_claim | inference | weak_inference>
proposed_anchors: <list of anchor operations; see augur-graph-schema.md>
reasoning: <why the lens extracted this>
```

`confidence_band` is the lens's self-assessment of how solid the signal is:

- `hard_datum` — direct numerical or factual observation (a USGS earthquake, an IMF gold reserve number, an ADS-B flight path).
- `reported_claim` — sourced statement of fact from a credible source ("Reuters reports that Source X says Y").
- `inference` — the lens drew a conclusion from observable evidence ("Three articles describe traffic disruption at Port X; the lens concludes Port X operations are constrained").
- `weak_inference` — the lens is reaching, but the signal might still be worth tracking ("This article hints at a position change but doesn't state it directly").

Confidence band is independent of source weight. Source weight is about how reliable the source is in general; confidence band is about how solid this specific signal is given what's actually in the payload.

### Lens execution

Lenses run **in parallel** for each payload. Lens A reading a payload does not see Lens B's output for the same payload. This is intentional — it prevents lens cross-contamination and keeps each lens narrowly scoped to its priors.

After all lenses run, their signals are merged into a single batch for Tier A storage.

### What lenses do not do

- They do not deduplicate against other lenses' output (Tier A handles cross-lens convergence).
- They do not check the graph for existing nodes (the anchoring stage handles entity resolution).
- They do not commit to graph mutations (they propose; the anchoring stage decides).
- They do not retrieve additional context (each lens sees only its assigned payload, by design).

---

## Stage 3: Tier A — Raw signal store

Working memory. The 90-day buffer between extraction and graph update.

### What Tier A does

**Stores signals.** Every signal from every lens for every payload. Schema as defined above.

**Vectorizes claims.** The `claim_text` of each signal is embedded into a vector index (pgvector) for similarity search.

**Detects intra-lens duplicates.** When two signals from the same lens, from different payloads, have high claim-vector similarity (e.g., cosine > 0.92), they are clustered. The cluster represents a single underlying claim with multiple supporting payloads.

**Detects cross-lens convergence.** When signals from multiple lenses produce semantically similar claims, the convergence is flagged. This is one of the strongest signal types in the system — when the commodities lens and the financial lens both extract the same underlying observation from different framings, the claim is robust.

**Detects cross-perspective convergence.** When signals from different perspectives (US/EU vs India vs China) produce semantically similar claims, the geographic breadth is flagged. The number of perspectives reporting a claim is a separate dimension from the number of sources.

**Calculates signal strength.** A composite score per claim cluster:
- Number of corroborating signals (with diminishing returns past ~5).
- Number of perspectives represented (with diminishing returns past ~3).
- Source weights of the originating payloads (from `augur-sources.md`).
- Confidence bands of the underlying signals.

This composite is not a probability; it's a heuristic the anchoring stage consumes to decide whether a signal cluster warrants graph mutation.

**Ages out.** Signals older than 90 days that were not anchored to the graph are archived (moved out of hot storage but retained in cold archive) and removed from the vector index.

### Tier A queries

The anchoring stage queries Tier A for batches matching certain criteria:
- Signal clusters with strength above a threshold.
- Signal clusters touching certain entities or graph regions.
- Signal clusters flagged as cross-perspective convergent.

The disconfirmation lens queries Tier A for:
- Recent signals that touch any of the top-N highest-weight edges in the graph.

The operator UI queries Tier A for:
- Recent signals, filterable by lens, source, perspective.
- Signal clusters and their composition.

### What Tier A does not do

- It does not modify the graph.
- It does not interpret signals beyond clustering.
- It does not assign confidence to clusters as a whole (it composites the underlying signals' bands).
- It does not promote signals to long-term memory; only the anchoring stage does that.

---

## Stage 4: Anchoring

The bridge from working memory to long-term memory. The LLM call that has the most leverage on graph quality, and therefore the most care.

### How anchoring runs

**Trigger:** anchoring runs on a schedule (initially hourly, tunable) and on demand when a high-strength signal cluster appears in Tier A.

**Batch formation:** the anchoring orchestrator pulls recent signal clusters from Tier A and groups them into batches. Batching criteria:
- Geographic and topical adjacency (clusters about the same entity or region go in the same batch).
- Maximum batch size of ~20 clusters to keep context manageable.
- Minimum batch age (signals must have had at least N hours in Tier A so corroboration can accumulate; prevents anchoring on hot-take individual signals).

**Subgraph snapshot:** for each batch, the orchestrator pulls a relevant subgraph from Tier B — the existing nodes and edges that touch the entities mentioned in the batch's signals, plus their immediate neighbors. This becomes the LLM's context for understanding what already exists.

**LLM call:** the anchoring prompt receives:
1. The batch of signal clusters with all their underlying signals.
2. The relevant subgraph in a compact representation.
3. The graph schema (loaded once, cached, included in system prompt).
4. Instructions for producing proposed_anchors.

The model is asked to produce a list of `GraphUpdateEvent` proposals consistent with the schema and the proposed_anchors format.

**Model class:** mid-strength to strong. This stage needs to reason about graph context, not just extract claims. A weaker model produces lower-quality anchoring even if its extractions were fine.

### The anchoring prompt — structure

The prompt has five sections:

1. **Role and goal.** "You are the anchoring stage of Augur. Your job is to convert a batch of signal clusters into structured graph updates, respecting the schema and the existing graph."
2. **Schema definition.** Compact representation of node types, edge types, weight bands, and the proposed_anchors operation types. (Cached, referenced by ID rather than re-sent every call.)
3. **Current subgraph.** A representation of existing nodes and edges in the relevant neighborhood, with their current weights and a brief description each.
4. **Signal batch.** The signal clusters with their underlying signals, source perspectives, confidence bands, and reasoning.
5. **Output instruction.** Produce a JSON list of GraphUpdateEvent operations. For each, include reasoning and (for create_edge) falsification_criteria. Reject anchors that don't meet the schema's requirements.

### Anchoring discipline

The anchoring LLM is instructed to err on the side of:
- **Reusing existing nodes.** If a signal could reasonably anchor to an existing node, it must — not create a near-duplicate.
- **Reusing existing edges.** Strengthening or weakening an existing edge is preferred over creating a parallel one.
- **Conservative weight bands.** A single signal cluster rarely warrants a `strong` weight on a new edge. `provisional` or `weak` are appropriate for new edges; bands strengthen as additional corroborating signal accumulates.
- **Explicit falsification criteria.** Any new edge must specify what would weaken it. The applier rejects edges without this.

### The applier

The applier is **plain code**, not an LLM. It receives the LLM's proposed_anchors output and:

1. **Validates schema.** Reject operations with invalid node/edge types, missing required fields, or out-of-range weight bands.
2. **Resolves aliases.** Normalize entity names against the alias table; rewrite proposed anchors to use canonical node IDs.
3. **Detects duplicates.** If a proposed create_node would duplicate an existing node (after alias resolution), rewrite to an update_node or skip.
4. **Validates references.** Every node_id and edge_id referenced must exist (or be a forward reference resolvable within the batch).
5. **Applies invariants.** Weight bands within allowed set; edges have falsification_criteria; no orphan edges.
6. **Writes to Tier B.** Successful operations are applied. The graph update events themselves are stored as immutable history records with timestamps from the originating signals' content_timestamps.
7. **Logs rejections.** Rejected operations are stored with rejection reasons for operator review.

The applier is what makes the system safe. The LLM proposes; the applier disposes. No graph mutation happens outside the applier.

---

## Stage 5: The graph as continuous state

Tier B is the durable record. Stage 4 writes to it; stages 6 and 7 read from it.

The graph schema document (`augur-graph-schema.md`) is the source of truth for Tier B's structure. The signal pipeline document only commits to how the pipeline interacts with it:

- All writes go through the applier.
- All reads use schema-compatible queries (Cypher via AGE, or SQL where graph traversal isn't needed).
- Every read can be scoped to a historical timestamp via the `as_of` parameter (see replay mode in `augur-architecture.md`).
- The graph is never bulk-mutated outside the applier, even by the operator. Operator edits go through a dedicated `operator_override` event type that records the human edit alongside its reasoning.

---

## Stage 6: Disconfirmation pass

The architectural countermeasure against confirmation bias. Runs periodically (initially weekly).

### How it runs

**Selection.** The disconfirmation orchestrator selects edges to challenge:
- Highest-weight edges that haven't been challenged in the last cycle.
- Edges whose supporting signals are getting old without recent corroboration.
- Edges flagged by users or operators as suspect.

**Challenge prompt.** For each selected edge, a strong-model LLM call:
1. Receives the edge with its source/target nodes, current weight band, supporting signals, current reasoning, and falsification criteria.
2. Receives a window of recent signals (last cycle period) from Tier A and recently anchored Tier B updates that touch the edge's neighborhood.
3. Is asked: *"Given this edge's falsification criteria, what evidence from the recent window would meet them? Cite specific signals. If no significant disconfirmation is found, say so explicitly and explain why the recent evidence does not weaken the edge."*

**Output.** Either:
- An `update_edge_weight` operation with `direction: weaken` and supporting signal IDs.
- An `add_disconfirming_signal` operation with the signal IDs that partially weakened the edge.
- A `no_disconfirmation_found` record with reasoning. This goes into Tier B as a `disconfirmation_pass_event` linked to the edge, recording that the edge was examined and survived.

**Application.** The applier processes disconfirmation outputs identically to anchoring outputs. The graph update event records that this change came from disconfirmation rather than from forward anchoring.

### Why the discipline matters

A graph with strong edges and no disconfirmation history is suspect. The disconfirmation pass produces durable records of the system having examined its own beliefs. Operators reviewing the graph months later can see *"this edge has been challenged 12 times and survived each time"* versus *"this edge was created 6 months ago and has never been re-examined."* The latter is a warning sign regardless of weight.

### Disconfirmation lens vs disconfirmation pass

The disconfirmation **lens** (from stage 2) runs inline during normal extraction and is opportunistic — it looks at each payload for incidental contradictions with the graph.

The disconfirmation **pass** (stage 6) runs periodically and is exhaustive — it deliberately selects high-weight edges and demands the system challenge them.

Both are needed. The lens catches occasional contradiction signal; the pass forces systematic review. Lens findings are often inputs to subsequent passes.

#### Design decision: separate lens vs property of every lens

The current design treats disconfirmation as a **distinct lens with its own scope and prompt**, parallel to the other lenses in the catalog. The alternative considered was to give every lens an embedded "bear case" capability — each lens, while doing its primary extraction, would also check whether what it's reading contradicts existing high-weight edges in its graph_scope.

The case for the current design (separate lens):

- Cleaner architecture. Each lens has one job. The disconfirmation lens is specialized for its task and can be tuned independently.
- The disconfirmation lens can use a stronger model than the cheap extraction lenses without paying that cost on every lens call.
- Easier to evaluate. Disconfirmation lens performance is a measurable thing on its own; mixing it into other lenses muddies that signal.
- Aligns with the principle that lenses are narrow and opinionated.

The case for the alternative (property of every lens):

- Every lens already has graph_scope and already reads the payload. Adding a "check for contradictions" instruction is one more step in an existing prompt rather than a new pipeline component.
- Avoids the second LLM pass over the same payload, reducing cost and latency.
- Catches contradictions that are domain-specific — a commodities-specific contradiction might be missed by a general disconfirmation lens but caught by the commodities lens that has the right priors.
- Distributes the disconfirmation discipline across the whole extraction layer rather than concentrating it.

**Current decision: keep them separate.** Simpler to build, easier to evaluate, and the per-domain argument can be addressed later by tuning the disconfirmation lens prompt to be domain-aware.

**Revisit trigger:** if calibration runs show that the disconfirmation lens is systematically missing domain-specific contradictions, or if inference cost on the two-pass approach becomes a real budget concern, reconsider folding disconfirmation into each lens's primary prompt. The architectural cost of the migration is small because lens prompts are already swappable.

---

## Stage 7: Read paths

Projection, the operator UI, and the user-facing graph view all read from Tier B. None of them write directly; they emit events through the applier if they need to record anything.

This document does not specify projection's internal logic in detail (that's a future document if projection grows complex enough to warrant one). It does specify projection's contract with the pipeline:

- Projection runs read-only against Tier B.
- Projection runs at an `as_of` timestamp, defaulting to now.
- Projection outputs are not durable unless the user explicitly saves them as `Scenario` nodes, which go through the applier.
- Projection is cheap to re-run; the system never caches projection outputs as authoritative.

---

## Signal type taxonomy and decay

Different kinds of signals decay at different rates. The system needs to model this explicitly.

| Signal type | Half-life | Notes |
|---|---|---|
| Spot price | days | Commodity prices, exchange rates, equity indices. |
| Flow rate | weeks | Shipping volumes, trade flows, capital flows. |
| Inventory level | months | Reserves, stockpiles, strategic petroleum reserves. |
| Policy statement | months to years | Until reversed or superseded. |
| Treaty / formal agreement | years | Persist until explicit dissolution. |
| Infrastructure status | years | Pipelines, ports, plants — change slowly. |
| Demographic trend | years | Migration, population, urbanization. |
| Structural relationship | indefinite | "Morocco produces phosphate" — true until structural change. |

Decay applies to edge weights, not to whether the signal is recorded. The weight contributed by a spot-price signal fades over days; the weight contributed by a treaty signal does not. The exact decay functions are per-signal-type configuration, tuned during calibration.

---

## What the pipeline deliberately does not do

- It does not predict. Projection is read-side and consumes the graph; it does not produce signals.
- It does not adjudicate between contradicting signals. It records contradictions as `contradicts` edges or as `Claim` nodes with mixed evidence; users and the disconfirmation pass adjudicate.
- It does not generate new sources. The source registry (`augur-sources.md`) is curated, not auto-discovered.
- It does not run on streaming data. Ingestion is batch-oriented with frequent cycles, not millisecond-latency streaming.
- It does not retry failed LLM calls indefinitely. After a small number of retries with backoff, a payload's extraction is marked failed and the operator UI surfaces it for review.

---

## Build implications

The shape of this document implies a build order:

1. **Ingestion and Payload storage** first. No LLM calls; just fetch, normalize, archive.
2. **One lens** (probably `commodities` — narrow, easy to validate). Tier A storage for its outputs.
3. **A trivial applier** that accepts hand-authored proposed_anchors and writes to Tier B. Lets you bootstrap the graph manually while extraction is still being tuned.
4. **Anchoring** that produces proposed_anchors from Tier A signals, going through the applier.
5. **Additional lenses** added one at a time, with calibration runs between each addition to confirm the new lens produces durable signal.
6. **Disconfirmation lens** (inline).
7. **Disconfirmation pass** (periodic).
8. **Projection** and read paths.

Each step is independently testable. The order is dictated by what the next step needs to read from. This sequence is elaborated in `augur-roadmap.md`.
