# Augur

A reasoning prosthetic for understanding the present and exploring plausible futures.

Augur ingests structured signals from the world — news, central bank flows, shipping, seismic activity, aircraft movements, mining production, commodity prices — and maintains a navigable causal graph that represents how those signals connect. The graph is something to think *with*, not something that thinks for you. It does not predict the future. It makes the present and its plausible futures more legible.

## What this repository contains

This repository is the design and implementation of Augur, a single-operator system built to run on a Hetzner VPS. The project is currently in the **design phase**, with foundational documents complete and implementation work beginning at Phase 0 of the roadmap.

## Project status

The design is settled enough to begin building. The eight foundational documents in `docs/` define the system from purpose through implementation sequence. Code is being written against these documents, not the other way around. When implementation reveals that a document is wrong, the document is updated before the code is committed.

## Where to start reading

If you have ten minutes, read **`docs/augur-vision.md`** first. Everything else inherits from it.

If you have an hour, read in this order:

1. **`docs/augur-vision.md`** — Why Augur exists. What it is and isn't.
2. **`docs/augur-architecture.md`** — The shape of the system. Storage, runtime, replay mode, observability.
3. **`docs/augur-graph-schema.md`** — Node types, edge types, weight bands, the proposed_anchors contract.
4. **`docs/augur-signal-pipeline.md`** — How raw inputs become graph updates. The lens catalog.
5. **`docs/augur-sources.md`** — Source tiers, perspective pools, the source registry.
6. **`docs/augur-calibration.md`** — Replay-mode methodology for tuning source weights.
7. **`docs/augur-presentation.md`** — The five-zoom interaction model. Home view, time scrubber, conversation.
8. **`docs/augur-roadmap.md`** — The twelve-phase build sequence.

Each document has a reading guide at the top that cross-references the others. The dependency chain flows roughly vision → architecture → schema → pipeline → sources → calibration → presentation → roadmap.

## The core ideas, very briefly

- **The graph is the product**, not a forecasting engine. Outputs are conditional and branching, never committed predictions.
- **Physical reality over narrative.** When shipping data, seismic feeds, and central bank flows say one thing and news reporting says another, physical data wins.
- **Multi-lens extraction.** The same payload is read by multiple LLM lenses with different priors. Each lens produces signals; cross-lens convergence is one of the strongest signals.
- **Multi-perspective ingestion.** Nine perspective pools (US/EU, India, China, Russia, Gulf and Levant, Nordic, Latin America, Africa, Southeast Asia and Pacific). Same event reported differently across perspectives is itself signal.
- **Two-tier memory.** Tier A is 90-day working memory for deduplication. Tier B is the graph itself, the durable long-term memory.
- **LLM proposes, applier disposes.** No LLM ever writes directly to the graph. A plain-code applier validates and applies graph mutations.
- **Disconfirmation is first-class.** A weekly pass actively challenges the highest-weight edges. Every edge has falsification criteria recorded at creation.
- **Replay mode is built in.** Every signal is timestamped at its content date, not its fetch date. The entire system can be queried "as of" any historical moment.

## Getting started with implementation

Phase 0 of the roadmap (infrastructure foundation) is the entry point. See `docs/augur-roadmap.md` for the full phase sequence.

Prerequisites:

- A Linux server (current target: Hetzner VPS) with Docker and Docker Compose installed.
- A SearXNG instance accessible to the application (currently running on the same VPS).
- A Langfuse instance for LLM observability (currently running on the same VPS).
- An OpenRouter API key for production model access.
- A second OpenRouter API key restricted to free-tier models, reserved for the conversation layer.
- Object storage (Hetzner Object Storage or Backblaze B2 recommended) for off-host backups.

The first phase builds the infrastructure foundation without any Augur-specific logic. See `docs/augur-roadmap.md` Phase 0 for the specific deliverables.

## Operator commitments

Augur is a single-operator system. Running it well requires sustained, manageable attention:

- **Daily**: a brief look at cost and operational health.
- **Weekly**: review of anchoring quality on a sample of recent batches, review of disconfirmation outputs.
- **Quarterly**: review of source weight changes proposed by ongoing calibration, deliberate source admission and retirement decisions.
- **As needed**: dedicated calibration runs after significant lens or schema changes.

If these routines become hours-long rather than minutes-long, the automation is wrong. See `docs/augur-roadmap.md` "Failure modes to watch for" for what burnout in Phase 7 looks like.

## What this project is not

- Not a business. Augur exists to support transparency and agency, not to generate revenue.
- Not a news aggregator. News is an input; the graph is the product.
- Not a forecasting engine. Augur does not predict the future.
- Not a general-purpose information product. It's built for a small audience of systems thinkers who want a tool to think with, not be told things by.
- Not a multi-tenant system. One operator, one graph.

See `docs/augur-vision.md` for the full statement of what Augur is and isn't.

## Authorship and intent

Augur is built by one person, primarily for that person's own use, with the design assumption that the audience is people who think similarly. The choice of sources, the structure of the graph, and the framing of what counts as a useful output all reflect the priors of its author. This is acknowledged rather than hidden.

## License

To be decided. The project is currently in private development.

## Working on this codebase

If you are an LLM agent (Claude Code, Cursor, or another coding agent) working on this repository, **start by reading `AGENTS.md`** in addition to the documents above. It contains operating instructions specific to working efficiently and accurately on this project.

If you are a human collaborator, `AGENTS.md` is also worth a read — it documents the conventions and review patterns that keep the codebase honest.
