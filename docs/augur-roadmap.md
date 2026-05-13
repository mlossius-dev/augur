# Augur — Roadmap

*The build sequence. What gets built in what order, why, and what each phase produces. Inherits from every other document; this is where they converge into a plan.*

---

## Reading guide

This document is the practical answer to "where do I start, and what's next." It sequences the work across phases, explains the dependency logic, and names what each phase produces and what success looks like for each.

Cross-references:
- Vision and design principles → `augur-vision.md`
- System shape → `augur-architecture.md`
- Graph structure → `augur-graph-schema.md`
- Pipeline stages → `augur-signal-pipeline.md`
- Source registry → `augur-sources.md`
- Calibration methodology → `augur-calibration.md`
- User interface → `augur-presentation.md`

---

## Sequencing principles

Four rules shape the order:

1. **Substrate before structure.** The signal layer is built before the graph layer, which is built before the projection layer. Each tier inherits from the one below it; building higher tiers on shaky lower tiers produces confident-sounding nonsense.

2. **Validate before scaling.** Each phase has a small validation gate before the next phase begins. A pipeline that works for one lens against one source corpus is the proof needed before adding lenses and sources.

3. **Calibration is a phase, not an afterthought.** Source weights and lens scores must be empirically grounded before the system runs live. The replay-mode calibration phase sits before live operation, not after.

4. **Presentation is built last and incrementally.** The visual interface and conversation layer are the final phases, deliberately. The system must produce a graph worth looking at before any time is spent making it look good. Even within the presentation phase, the minimal home view comes well before the conversation layer.

The temptation throughout will be to skip phases or build them out of order. The most common failure mode of projects like Augur is starting with the projection layer or the visual layer because they are the exciting parts. Resist this. The substrate is where the project succeeds or fails.

---

## Phase 0 — Infrastructure foundation

*Get the ground ready. No Augur logic yet.*

**Duration estimate:** 1-2 weeks.

**What gets built:**

- Docker Compose stack on the Hetzner VPS with Postgres 16+ (with pgvector, pg_trgm, PostGIS, Apache AGE extensions enabled), Redis (in case it's needed later for orchestration), and a FastAPI application skeleton.
- Connection to the existing SearXNG instance on the same VPS.
- Connection to the existing Langfuse instance on the same VPS.
- OpenRouter integration with a thin LLM client abstraction that emits Langfuse traces on every call.
- A second OpenRouter key for free-tier-only access, reserved for the conversation layer when it is built later.
- Off-host backup configuration for the Postgres database to object storage.
- Basic operator CLI for running ad-hoc tasks.
- Environment configuration management (secrets, model routing, rate limits) handled through env files and Docker secrets.
- Logging and structured error handling at the application skeleton level.

**Phase 0 succeeds when:**

- The application starts cleanly from `docker compose up`.
- A test LLM call through the client abstraction succeeds and shows up in Langfuse.
- A test Postgres query through the application returns expected results, exercising at least the pgvector and AGE extensions.
- Off-host backups are confirmed running and restorable.

**Phase 0 does not include:**

- Any Augur-specific data models.
- Any extraction, anchoring, or graph logic.
- Any user-facing interfaces.

**Why this phase is separate:** infrastructure work is its own skill and its own failure mode. Mixing it with logic work means infrastructure bugs masquerade as logic bugs. Doing it first and proving it works gives every subsequent phase a stable foundation.

---

## Phase 1 — The data model and applier

*Build the durable layer first. No LLMs yet.*

**Duration estimate:** 2-3 weeks.

**What gets built:**

- The full graph schema in Postgres tables. All six node types (Entity, Condition, Event, Quantity, Scenario, Claim) and their fields per `augur-graph-schema.md`. All nine edge types with their full required fields including `falsification_criteria`.
- AGE configuration with appropriate vertex and edge labels matching the schema.
- The Tier A signal store schema with pgvector configured for claim-level embeddings.
- The Tier B graph store schema with weight history as append-only time-series tables.
- The applier as plain Python code that accepts `proposed_anchors` JSON and writes to Tier B with full validation. Rejection logic, alias resolution, schema invariant enforcement.
- The alias table starting with maybe 100 manually-entered canonical names (major countries, currencies, central banks, commodities). The seed for entity resolution.
- A Python API for the applier (`applier.apply(events: list[GraphUpdateEvent])`) callable from scripts and tests.
- Hand-authored loading of the worked-example seed graph from `augur-graph-schema.md` into Tier B, exercising every node type and edge type.

**Phase 1 succeeds when:**

- The fertilizer→food chain from the schema document is loaded as actual graph data and queryable through both SQL and Cypher.
- Hand-authored `proposed_anchors` JSON can be passed through the applier and correctly mutates the graph.
- Invalid anchors are rejected with clear logged reasons.
- Edge weight history is correctly appended on every update.
- A test query "give me the graph as it existed yesterday" returns the historical state correctly via replay-mode reads.

**Phase 1 does not include:**

- Any extraction logic.
- Any LLM calls (the applier is plain code).
- Any UI.

**Why this phase comes second:** the applier is the gate to graph mutation. Building it before extraction means extraction can be developed against a known-good gate. Building the gate after extraction means every extraction bug is also a graph corruption bug.

---

## Phase 2 — Ingestion and one lens

*Get content flowing. Prove extraction works with one well-defined lens.*

**Duration estimate:** 2-3 weeks.

**What gets built:**

- The ingestion layer per `augur-signal-pipeline.md` stage 1: fetchers for HTTP and RSS, SearXNG-mediated search, Playwright integration for JS-rendered sites, structured-data API clients for one or two Tier 0 sources (USGS earthquakes and FRED are good starting picks — clean APIs, structured data, low friction).
- Payload normalization to the canonical `Payload` shape with `content_timestamp` and `fetched_at` as separate fields.
- Payload archival to local filesystem with object-storage backup.
- The first lens: **`commodities`**. Narrow enough to validate the architecture, broad enough to produce real signal volume. Configured with its system prompt, signal schema, graph scope, and starting model class.
- Tier A signal storage with pgvector indexing.
- Lens-level deduplication: detect when two signals from the same lens cluster on the same claim.
- A small operator CLI for inspecting recent ingestions, recent extractions, and recent rejections.
- Per-source configuration loaded from a structured file (`sources.yaml`) starting with maybe 10-15 sources across 3 perspective pools (US/EU, India, China). Tier 0, 1, and 2 sources only.

**Phase 2 succeeds when:**

- A scheduled run fetches new content from configured sources every hour without manual intervention for 48 hours straight.
- The commodities lens extracts signals from real content, with the operator able to read recent extractions and judge whether they're sensible.
- Tier A clustering correctly groups duplicate Reuters/AP/AFP wire reports as one cluster.
- Cost per ingestion cycle is tracked in Langfuse and within budget.
- An operator review of 50-100 random extractions finds at least 60% of them to be reasonably-extracted signals (not random noise, not hallucinated content).

**Phase 2 does not include:**

- Anchoring (signals stay in Tier A; the graph is not yet being mutated by extraction).
- Other lenses (those come in phase 4).
- Disconfirmation.
- Tier 3 and Tier 4 sources.

**Why this phase comes third:** ingestion plus one lens is the smallest end-to-end slice that produces real signal volume. Validating the slice with the operator's own eyes before adding more lenses prevents amplifying extraction failures across the whole catalog.

---

## Phase 3 — Anchoring

*Connect extraction to the graph.*

**Duration estimate:** 2-3 weeks.

**What gets built:**

- The anchoring orchestrator per `augur-signal-pipeline.md` stage 4: batch formation, subgraph snapshot, LLM call configuration.
- The anchoring prompt template with all five required sections (role, schema reference, subgraph context, signal batch, output instruction).
- The applier integration: anchoring outputs flow through the same applier from phase 1.
- Operator UI for reviewing recent anchoring batches, including rejections and the rejected reasons.
- Cost and latency observability for the anchoring stage specifically (more expensive than extraction; needs distinct dashboards).

**Phase 3 succeeds when:**

- The commodities lens's extracted signals are batched and anchored hourly without manual intervention.
- The graph grows beyond the seed graph through real anchoring of real signals.
- An operator review of 50-100 anchoring decisions finds that at least 70% of them produced reasonable graph mutations (correct node reuse, sensible edge creation, appropriate weight bands, valid falsification criteria).
- The applier rejection rate is low (rejecting most anchoring outputs means the prompt is broken or the model is too weak).
- The graph at the end of the phase is recognizably a representation of the commodities space the lens has been watching.

**Phase 3 does not include:**

- Disconfirmation (the graph just grows; nothing challenges it yet).
- Calibration (source weights remain at tier baseline).
- Multiple lenses (still just commodities).

**Why this phase comes fourth:** anchoring is the most LLM-sensitive part of the pipeline. Getting it working with one lens before adding others means the anchoring prompt can be tuned against a known signal source. Adding lenses before anchoring is solid means multiple confounded variables when things go wrong.

---

## Phase 4 — The remaining lenses

*Build out the full extraction layer.*

**Duration estimate:** 3-4 weeks.

**What gets built:**

- The remaining six lenses per `augur-signal-pipeline.md`: `financial`, `geopolitical`, `physical_world`, `regulatory`, `narrative_divergence`, and inline `disconfirmation`.
- Each lens added one at a time, with its own validation period before the next is added.
- Expansion of the source registry as new lenses surface gaps — for instance, the physical_world lens drives addition of ADS-B, AIS, and EMSC sources; the financial lens drives addition of IMF COFER, WGC Goldhub, and TIC data.
- The narrative_divergence lens drives expansion of perspective pools: Russia, Gulf and Levant, Nordic, and the three Global South pools (Latin America, Africa, Southeast Asia and Pacific) come online with their source sets during this phase.
- Per-lens operator review and tuning before each subsequent lens is added.
- Tier A composite scoring (the per-claim cluster strength score from `augur-signal-pipeline.md`) becomes meaningful now that multiple lenses are producing convergent signals.

**Phase 4 succeeds when:**

- All seven lenses are running in parallel against all configured sources.
- Cross-lens convergence detection is producing meaningful signal (multiple lenses extracting the same underlying claim from different framings).
- Cross-perspective convergence detection is producing meaningful signal (multiple perspective pools reporting similar claims).
- The graph spans multiple domains (commodities, finance, geopolitics, physical world, regulatory) rather than being concentrated in one.
- Operator review confirms each lens is producing useful signal at acceptable rate and cost.

**Phase 4 does not include:**

- Calibration (still using tier baselines; no empirical source weights yet).
- The disconfirmation pass (inline disconfirmation lens runs, but the periodic pass does not).
- The projection layer.
- Any user-facing UI beyond the operator's review tools.

**Why this phase comes fifth:** adding lenses incrementally surfaces lens-specific failure modes one at a time. Adding all seven at once would conflate which lens is responsible for which failure.

---

## Phase 5 — The disconfirmation pass

*Add the architectural countermeasure against confirmation bias.*

**Duration estimate:** 1-2 weeks.

**What gets built:**

- The periodic disconfirmation pass per `augur-signal-pipeline.md` stage 6: edge selection, challenge prompts, output handling through the applier.
- A separate disconfirmation cadence (initially weekly) running independently of the main ingestion-extraction-anchoring cycle.
- Operator review of disconfirmation outputs: which edges were challenged, which were weakened, which survived with `disconfirmation_pass_event` records.
- Disconfirmation-specific cost and quality dashboards in Langfuse.

**Phase 5 succeeds when:**

- The weekly disconfirmation pass runs without intervention and produces actionable outputs.
- Some high-weight edges in the graph have been challenged and either survived (with `no_disconfirmation_found` records) or been weakened (with weight history showing the reduction).
- The operator reviews disconfirmation outputs and confirms they're substantively challenging the graph, not rubber-stamping.
- The graph now has audit trail: any edge can be queried for "when was this last challenged, and what happened."

**Phase 5 does not include:**

- Calibration runs (those come next, now that the full pipeline is in place).
- The projection layer.
- User-facing UI.

**Why this phase comes sixth:** the disconfirmation pass is the integrity discipline for the graph. Adding it before the graph has interesting content to challenge is premature; adding it after the graph has been growing unchallenged for months means the first pass has too much accumulated bias to address. Adding it once the full lens catalog is operating but before calibration runs means the disconfirmation discipline is in place when calibration begins.

---

## Phase 6 — First calibration run

*Tune source weights and lens parameters with empirical data.*

**Duration estimate:** 2-3 weeks, including execution time.

**What gets built and run:**

- Replay-mode infrastructure: the `as_of` parameter wired through all read paths, sandbox prompt templates for extraction, model selection layer configured with cutoff-aware filtering.
- Historical payload sourcing for the calibration window. Per `augur-calibration.md`, the recommended first window is September 2022 - June 2023 (European energy crisis arc). Sources whose archives are accessible are used; sources without good historical access are excluded from this run.
- The replay execution itself: weeks of processing time replaying historical signals through the full pipeline.
- The signal-survival metric per `augur-calibration.md`: outcome tracking, per-source and per-lens scoring, the conservative weight update formula.
- Spot-check tooling for look-ahead bias detection.
- Calibration report generation: a structured operator-facing summary of source weight changes, lens scores, leakage detection results, and flagged sources and lenses.
- Operator review and approval of weight updates before they apply to the source registry.

**Phase 6 succeeds when:**

- The calibration run completes end-to-end without major pipeline failures.
- Source weight stratification emerges (some sources move significantly, others don't, in a distribution that holds up to operator review).
- Lens scores reveal at least one weak lens for prompt revision.
- Leakage detection finds bias in less than ~5% of sampled extractions.
- The graph at the end of the replay window is recognizable as a reasonable representation of the European energy crisis arc.
- Updated source weights are committed to the source registry.

**Phase 6 does not include:**

- Live operation (that begins after this).
- The projection layer.
- User-facing UI.

**Why this phase comes seventh:** calibration is the empirical grounding that lets live operation start with non-arbitrary source weights. Running live without calibration means months of operation with tier-baseline weights, with no way to distinguish high-quality sources from low-quality ones except operator guesswork.

---

## Phase 7 — Live operation begins

*The system runs against current sources, with calibrated weights.*

**Duration estimate:** ongoing; first 4-6 weeks before phase 8.

**What gets built and operationalized:**

- Live ingestion against the full source registry with calibrated weights.
- The full pipeline running continuously: ingestion → extraction → Tier A → anchoring → Tier B → disconfirmation.
- Ongoing signal-outcome tracking per `augur-calibration.md` for live-operation calibration that runs in the background.
- Operator monitoring routines: daily check of cost, weekly check of anchoring quality, weekly review of disconfirmation outputs.
- Source weight tuning based on ongoing live calibration data accumulating over weeks.
- Graph stewardship: the operator's primary ongoing responsibility is keeping the source registry healthy and the lens prompts calibrated. The graph itself grows on its own.

**Phase 7 succeeds when:**

- The system runs for at least 4 consecutive weeks without intervention required for operational issues.
- The operator can read the graph and find it informative — meaning, looking at the graph tells the operator something they didn't already know from reading the news directly.
- Cost is sustainable within the operator's chosen budget.
- The graph passes the "informative density" test: a domain expert reading a graph subset within their domain finds it largely accurate and at least somewhat insightful.

**Phase 7 does not include:**

- The projection layer.
- The user-facing UI.
- The conversation layer.

**Why this phase comes eighth:** live operation must be running and stable before presentation work begins. The presentation layer should be built on a system that produces a graph worth presenting, not on a system whose graph is still being shaped.

---

## Phase 8 — Minimal presentation

*Build the home view and the reasoning view. Web interface, no conversation yet.*

**Duration estimate:** 4-6 weeks.

**What gets built:**

- The web UI as a separate frontend talking to the FastAPI backend.
- The home view's top three regions per `augur-presentation.md`: the world's trajectory (level 1), the rate and acceleration (level 2), what changed in the last 24 hours (level 3).
- The reasoning view (level 5) reachable by drilling in from any element of the home view.
- The time scrubber as a persistent affordance, initially with 12 months of history available.
- The aesthetic principles from `augur-presentation.md` applied: research-tool density, calm typography, no notifications, no engagement metrics.

**Phase 8 succeeds when:**

- The operator can open Augur in a browser and answer "is the world improving or worsening" in seconds, "how fast" in the next moment, and "what changed today" in a glance.
- The drill-down from any home-view element to the underlying reasoning works smoothly.
- The time scrubber moves through historical state without visible failures.
- The interface looks like a research tool, not a news app.

**Phase 8 does not include:**

- The topic view.
- The geographic-scoping "your context" level.
- Projection.
- The conversation layer.

---

## Phase 9 — Topic view and geographic scoping

*Fill in the middle layers of the presentation hierarchy.*

**Duration estimate:** 2-3 weeks.

**What gets built:**

- The topic view per `augur-presentation.md`: prose summary, active conditions, recent changes (7-day window), focused subgraph diagram, related topics.
- Topic curation as an operator activity: clustering graph regions into named topics that surface as entry points on the home view.
- Geographic scoping via browser geolocation: the level-4 "in your context" region of the home view, scoping the trajectory and changes to the user's regional subgraph.
- Operator-configurable region definitions tied to perspective pools and major entity clusters.

**Phase 9 succeeds when:**

- A user from Tønsberg sees Nordic-relevant and European signal foregrounded; a user from elsewhere sees their regional view foregrounded.
- Topic views provide a natural "settle in and read about this" experience.
- The interface is now functionally complete for non-conversational use.

---

## Phase 10 — Projection

*Add the branching-future exploration affordance.*

**Duration estimate:** 3-4 weeks.

**What gets built:**

- Projection algorithm: graph walks from activated conditions outward, producing conditional branches with ordinal weights.
- Projection view per `augur-presentation.md`: branching tree, weight bands per branch, fragile-link identification, falsification signals per branch.
- Scenario saving: user-created `Scenario` nodes that capture the graph state at the time of projection.
- Operator review of projection outputs to confirm they're producing useful conditional thinking rather than confident-sounding nonsense.

**Phase 10 succeeds when:**

- The operator can ask "if X persists, what becomes more likely" from any condition node and get a branching answer that respects the schema's principles (multiple branches, ordinal weights, explicit fragile links).
- Saved scenarios survive graph evolution: opening a saved scenario weeks later shows both the original projection and how the underlying conditions have evolved since.

---

## Phase 11 — Conversation layer

*Add the natural-language affordance, grounded in the graph.*

**Duration estimate:** 3-4 weeks.

**What gets built:**

- The conversation layer per `augur-presentation.md`: natural-language interface grounded in current graph state, using the dedicated free-tier OpenRouter key.
- Conversation grounding: every response cites specific nodes, edges, or signals. The conversation refuses to answer questions the graph cannot support.
- Per-session conversation history (not persistent).
- The conversation entry point integrated into the home, topic, and reasoning views.
- Cost discipline: conversation operates within a separate, smaller budget than the main pipeline.

**Phase 11 succeeds when:**

- The operator can ask questions of Augur in natural language and get useful answers grounded in graph state.
- Cost per conversation session is acceptable on free-tier models.
- The conversation never tells the user something the graph does not support.
- The interface is now functionally complete per `augur-presentation.md`.

---

## After phase 11 — Ongoing operation

The project does not end with phase 11. After the full presentation is in place, the ongoing work is:

- **Continuous source curation.** Adding new sources, retiring stale ones, adjusting weights based on ongoing calibration.
- **Lens evolution.** Tuning prompts based on what calibration reveals, adding new lenses as domains expand, retiring lenses that don't pay off.
- **Schema evolution.** Periodically revisiting whether node and edge types still fit the graph, with deliberate migrations when changes are warranted.
- **Periodic dedicated calibration runs.** Quarterly or as needed, beyond the continuous live calibration.
- **Graph stewardship.** Reading the graph regularly, noticing where it gets stuck, intervening when disconfirmation isn't catching something the operator can see.

The project is not a product to be finished. It is a system to be lived with, tended, and gradually improved.

---

## Approximate timeline

The phase estimates sum to roughly **6-9 months** to reach a functionally complete system (through phase 11), assuming part-time work by one person with occasional collaborator help. The most realistic estimate is closer to the upper bound, given that several phases depend on operator review cycles that take real time.

| Phase | Duration | Cumulative |
|---|---|---|
| 0 — Infrastructure | 1-2 weeks | 1-2 weeks |
| 1 — Data model and applier | 2-3 weeks | 3-5 weeks |
| 2 — Ingestion and one lens | 2-3 weeks | 5-8 weeks |
| 3 — Anchoring | 2-3 weeks | 7-11 weeks |
| 4 — Remaining lenses | 3-4 weeks | 10-15 weeks |
| 5 — Disconfirmation pass | 1-2 weeks | 11-17 weeks |
| 6 — First calibration run | 2-3 weeks | 13-20 weeks |
| 7 — Live operation begins | 4-6 weeks before phase 8 | 17-26 weeks |
| 8 — Minimal presentation | 4-6 weeks | 21-32 weeks |
| 9 — Topic view and scoping | 2-3 weeks | 23-35 weeks |
| 10 — Projection | 3-4 weeks | 26-39 weeks |
| 11 — Conversation layer | 3-4 weeks | 29-43 weeks |

These estimates are illustrative. Real schedules will diverge based on what surfaces during build. The sequencing matters more than the durations.

---

## Failure modes to watch for

Predictable ways the roadmap can go wrong:

**Skipping phases or building out of order.** Especially tempting around the visual layer. If you find yourself wanting to build the home view before phase 8, that's the signal to refocus on whichever earlier phase is in fact incomplete.

**Treating calibration as optional.** It is the empirical grounding that distinguishes Augur from a system running on guessed weights. If calibration feels like a chore to defer, that's the failure mode asserting itself.

**Letting the source registry sprawl.** Adding sources is exciting; auditing them is not. A registry that grows from 50 to 500 sources without weight discipline becomes a noise generator. Source admission should remain deliberate forever.

**Letting the schema sprawl.** The graph schema document commits to small fixed type systems. The pressure to add node types and edge types will be constant. Most pressures should be resisted; intermediate nodes solve most apparent missing-edge-type problems.

**Building the conversation layer too early.** It's the most fun part. Done too early, it becomes a way to mask weaknesses in the visual layer. The roadmap puts it last for a reason.

**Burnout during phase 7.** Live operation is the long phase. The operator routines must be sustainable or they don't happen, and the project quietly stalls. Daily cost checks should take seconds; weekly anchoring reviews should take minutes. If they take hours, the routines need automation.

**Operator review fatigue during phases 2-5.** Each phase has an operator review gate. Skipping these gates because the volume is overwhelming is the failure mode that allows extraction failures to amplify. Sampling helps; lower thresholds for "good enough to pass" do not.

---

## When the roadmap is revisited

The roadmap should be revisited:

- At the end of each phase, briefly, to confirm the next phase's plan still fits.
- When a phase takes substantially longer than estimated, to understand why and whether subsequent phases inherit the same risk.
- When calibration produces findings that change architectural assumptions (e.g., a lens that should not exist, a perspective pool that should split, a node type that proves unused).
- When external context changes — for instance, model capabilities improve enough that decisions about which models to use for which stages need re-examination.

The roadmap is not a contract. It is a current best-understanding of how to sequence the work, written down so future-Morten has something to revise against rather than reconstruct.
