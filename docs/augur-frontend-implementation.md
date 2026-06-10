# Augur — Frontend Implementation Plan (Home v2)

*How the "Home v2" design in `Augur-frontend-from-Claude-Design/` gets wired to real backend data. A working plan, not a foundational document: it maps every design data point to a live endpoint, names the one backend change in scope, and parks the rest in an explicit backlog. Inherits from `augur-presentation.md` (the five-zoom interaction model it implements) and `augur-roadmap.md` (where presentation work sits).*

---

## Reading guide

This document is the bridge between a settled visual design and the already-built presentation API. It exists because the design ships with a fully **hardcoded** dataset, and someone (human or agent) needs an exact, honest map of which of those data points are real, which are derivable, and which are invented.

It does not specify pixel layouts or a component library — those follow the design files. It specifies **what data backs each surface, and where that data comes from.**

Cross-references:
- The interaction model this design implements → `augur-presentation.md` (the five zoom levels)
- Replay mode that powers the time scrubber → `augur-architecture.md`
- The endpoints referenced here → `src/augur/api/` and `src/augur/presentation/`
- The existing (working) frontend that already consumes these endpoints → `static/js/`

---

## Scope and decisions

Recorded decisions from the operator (2026-06-10):

1. **Design target:** `home-v2.jsx` ("Home v2") — the refined layout (verdict dial · domain cards · causal threads · your-latitude · live header · scrubber). The original `app.jsx` and the three metaphor explorations (herbarium / astrolabe / garden) share the **identical data model**, so this mapping applies to any of them if the direction changes later.
2. **Backend gaps:** **`/api/status` only.** Add the live-status endpoint; **defer** the other small gaps (`impact_rank` exposure, per-topic edge count, change→topic tagging).
3. **Fabricated content:** make it **real eventually** (build backend for it), but **each item needs its own proper plan, drafted after the frontend is done.** Until then these render as clearly non-live placeholders. **→ Reminder owed to the operator once the frontend is complete (see Backlog §B).**
4. **Deliverable:** this document, committed to the branch.

Design scaffolding excluded from the product entirely: `design-canvas.jsx` and `tweaks-panel.jsx` (artboard framing and theme knobs — design-time tooling, not product).

---

## The structural insight

The design is **faithful to `augur-presentation.md`.** Its five regions are the document's five zoom levels verbatim:

| Design region (Home v2) | Zoom level (`augur-presentation.md`) |
|---|---|
| Verdict dial — "Is the world improving?" | Level 1 — current state across five dimensions |
| Domain-card sparklines — velocity | Level 2 — "how fast?" |
| Domain-card ledgers / causal threads | Level 3 — "what changed in 24h?" |
| Your latitude | Level 4 — "what does this mean for me?" (geolocation-only) |
| Causal threads (topics) | Level 5 — "where to look closely" |

Because the model is settled, this is a **wiring + gap-closing** job, not a redesign. The existing `static/js/` frontend already consumes every endpoint named below and is the reference implementation for the data layer.

---

## Architecture: where the new frontend lives

- The new Home v2 UI is built **alongside** the current `static/` app, not on top of it, so the working UI is never broken mid-build. `src/augur/main.py` already serves `static/index.html` at `/` and mounts `/static`; the new build slots into the same mechanism (e.g. a parallel route or a replacement of `index.html` only when ready).
- The design files use in-browser Babel + CDN React (fine for a mockup). For production, the JSX is compiled ahead of time or ported to the existing vanilla-JS pattern in `static/js/`. **Decision deferred to build start** — it does not affect the data mapping.
- A thin **API adapter layer** is the heart of this work: it fetches the real endpoints and translates their shapes into the props the design components expect (band casing, direction polarity, sparkline cadence). The design's hardcoded `AUGUR_DIMS` / `AUGUR_CHANGES` / `AUGUR_TOPICS` / `AUGUR_LOCAL` globals are replaced by this adapter's output.

---

## Master mapping — design data point → real data

Disposition legend:
**LIVE** — endpoint returns it today ·
**DERIVE** — raw data exists; transform client-side ·
**STATUS** — covered by the in-scope `/api/status` endpoint ·
**DEFER-GAP** — small backend gap, deferred (frontend degrades gracefully) ·
**DEFER-REAL** — fabricated; on the "make-it-real" backlog (§B).

### Zoom 1 — Verdict dial / domain cards

| Design data | Real source | Disposition |
|---|---|---|
| Dimension name | `GET /api/home` → `dimensions[].label` | LIVE (labels match `DIMENSION_LABELS`) |
| Short code ("GEO") | derive from `dimensions[].dimension` | DERIVE |
| State band | `dimensions[].state` | LIVE — lowercase + extra `unknown`; adapter maps 6→5 bands and handles `unknown` |
| Direction glyph (↗/↘/→) | `dimensions[].direction` (`improving`/`steady`/`worsening`/`unknown`) | DERIVE — **mind polarity**: an *improving* direction is the "up/good" glyph |
| Composite needle + headline ("Not at present") | none (no composite endpoint) | DERIVE — average the five bands client-side |
| Latin binomial | none | DEFER-REAL (decorative; static label meanwhile) |
| Rate / Acceleration labels | none | DEFER-REAL (could derive from sparkline slope later) |
| Editorial note ("Real-rate compression…") | none | DEFER-REAL |

### Zoom 2 — Velocity sparklines

| Design data | Real source | Disposition |
|---|---|---|
| Sparkline series | `dimensions[].sparkline[]` = `{week_start, active_count, total_count}` | LIVE but **shape differs**: real series is ~13 **weekly** points rendered as the active/total ratio (see `static/js/home.js`). The design's "48 **monthly** absolute values" framing is not backed — adapter renders ratio-over-weeks; "48 months" copy is dropped. |

### Zoom 3 — What changed in 24h

| Design data | Real source | Disposition |
|---|---|---|
| Change body | `dimensions`-sibling `changes[].summary` | LIVE |
| Dimension tag | `changes[].dimension` / `.dimension_label` | LIVE |
| Timestamp | `changes[].occurred_at` | LIVE |
| Weight transition | `changes[].weight_before` / `.weight_after` | LIVE |
| Impact bar (5 segments) | `impact_rank` is computed in `presentation/changes.py` but **not serialized** | DEFER-GAP — for now derive an ordinal proxy from `change_type`, or omit the bar |
| Meta delta ("Brent +4.2%") | none (no market-price data in graph) | DEFER-REAL — show the real weight transition instead, meanwhile |
| "23 edges" downstream count | none | DEFER-REAL |
| Change→topic link ("root: Iran–Israel") | none (changes aren't topic-tagged) | DEFER-GAP — the Home v2 "causal threads" merge is deferred; list changes and topics separately for now |

### Zoom 4 — Your latitude (geolocation-only)

| Design data | Real source | Disposition |
|---|---|---|
| Region dimensions + changes | `GET /api/geo/scope?lat=&lon=&as_of=` → `dimensions[]`, `changes[]` | LIVE mechanism — **but not currently called from any UI**; needs browser-geolocation wiring (`augur-presentation.md` line 92 confirms geolocation is the intended input) |
| Region name | `region.display_name` | LIVE — but **continent-scale** ("North America"), per the seed in `005_topics.sql`. The design's "San Francisco, California" precision is **not** backed |
| Coordinates string | browser geolocation | DERIVE (client echo) |
| Hyper-local items (PG&E margin, Bay Area rent, zip codes) | none — `geo.changes[]` are keyword-filtered continental changes | DEFER-REAL — render the real region-filtered changes; drop the invented local specifics |

### Zoom 5 — Causal threads (topics)

| Design data | Real source | Disposition |
|---|---|---|
| Topic title / gist | `GET /api/topics` → `name` / `description` | LIVE |
| Node count | `topics[].node_count` | LIVE |
| Edge count ("487 edges") | none (topic summary has no edge count) | DEFER-GAP — show "N nodes" only for now |
| Weight (priority dot) | none (has `state`, `active_condition_count`) | DERIVE a priority proxy, or drop the dot |
| "7 of 28 sub-graphs" | topics list length | DERIVE |
| Topic drill-down | `GET /api/topics/{id}` (+ `/api/reasoning/...`) | LIVE |

### Cross-cutting surfaces

| Design element | Real source | Disposition |
|---|---|---|
| Time scrubber (time-travel) | `?as_of=` on every substantive endpoint | LIVE — fully real; `static/js/home.js` already wires it |
| "Ask Augur" conversation | `POST /api/conversation/query` → `answer`, `context.{n_nodes,n_edges}`, `model_used` | LIVE |
| Live clocks / date / roman numerals | client `Date` | DERIVE (no backend) |
| Live ingestion stats (payloads / signals / nodes / edges) | `monitoring/health.get_pipeline_health()` exists but is **not exposed via any `/api` route**; Home v2 currently **simulates** it with random drift | STATUS — new `/api/status` endpoint (see below); render only the windows that are genuinely available |
| Scrubber event markers ("SVB stress", "Yen carry unwind") | none | DEFER-REAL (static config meanwhile, or a future events feed) |
| "14,402 sources nominal" | `config/sources.yaml` has **28** sources; no API | DEFER-REAL (real count is ~28; expose when the sources item is built) |
| Operator id / subscription tier / edition / graph build / hash | none | DEFER-REAL **and flagged**: conflicts with `augur-presentation.md` line 106 ("no user preference settings… no saved searches") and single-operator design. Revisit needs a documents-first conversation |
| Confidence regime ("moderate · widening") | none | DEFER-REAL (could derive from direction spread) |

---

## In scope: the `/api/status` endpoint

The single backend change in this plan. It surfaces the live-system strip honestly by wrapping logic that already exists.

- **Route:** `GET /api/status` (new router, or fold into `api/health.py`).
- **Backed by:** `augur.monitoring.health.get_pipeline_health()` (and optionally `get_signal_flow()`), which already return everything needed and are currently only reachable from the CLI.
- **Genuinely available fields** (map these to the strip):
  - `signals`: `last_hour`, `last_24h`, `total`, `clustered`, `unclustered`
  - `payloads_24h`: `total`, `rejected`
  - `graph`: `live_nodes`, `live_edges`, `strong_edges`, `disputed_edges`
  - `pipeline`: `anchoring_backlog`, `stale_edges_for_disconfirmation`
  - `recent_jobs`: last run / status per job
- **Honesty constraints for the frontend strip:**
  - The design's "1h / 4h / 24h for all four metrics" grid is only **partly** real. `get_pipeline_health()` gives signals at 1h & 24h and payloads at 24h, but **no 4h window** and **no hourly node/edge deltas** (nodes/edges are current totals). Render the real windows; **drop the 4h column and the simulated drift**, or show current totals for nodes/edges.
  - The **source count** is intentionally *not* added here (it's a DEFER-REAL item, built with the sources backlog). It can be folded in trivially at that point via `ingestion/source_registry.get_enabled_sources()`.

---

## Frontend build sequence

- **Phase A — Foundation.** Stand up the Home v2 shell beside `static/`. Establish the design system (palette, type, `STATES` band colors) and the **API adapter** with the 6→5 band map and direction-polarity map.
- **Phase B — Live zooms, zero backend changes.** Wire Zoom 1 (dial + cards), Zoom 2 (sparklines), Zoom 3 (changes), Zoom 5 (topics), the scrubber (`as_of`), and Ask (conversation). This reproduces most of the design on real data.
- **Phase C — Geolocation (Zoom 4).** Browser geolocation → `/api/geo/scope`; render region-scoped dimensions/changes honestly (region name, not fabricated city items).
- **Phase D — `/api/status`.** Add the endpoint; replace the simulated live strip with real metrics (real windows only).
- **Phase E — Honesty pass + reminder.** Ensure every DEFER-REAL element reads as a clearly non-live placeholder (not as if it were real data). **Then surface Backlog §B to the operator** for per-item planning.

---

## Backlog

### §A — Deferred small gaps (data exists; not in this scope)

These degrade gracefully in the frontend until built:

1. **Expose `impact_rank`** on `/api/home` changes → enables the 5-segment impact bar. (~1 line in `_serialise_change`.)
2. **Per-topic edge count** in `get_topic_list` / `get_topic_detail` → enables "N nodes · M edges".
3. **Change→topic tagging** (join `changes[].target_id` to `topic_nodes`) → enables the Home v2 "causal threads" merge.

### §B — Make fabricated content real (operator chose this; **each needs its own plan, after the frontend**)

**Reminder owed:** once the frontend is complete, bring this list back to the operator and draft a proper plan per item. Some require a **documents-first** conversation before any code (per `AGENTS.md`).

| Fabricated element | Sketch of what "real" would require | Flags |
|---|---|---|
| Live stats 4h window + node/edge deltas | Track windowed counts (4h bucket; node/edge creation deltas) in `get_pipeline_health()` | Small; extends the in-scope endpoint |
| Rate / acceleration labels | Compute 1st/2nd derivative of the dimension sparkline; add fields to `/api/home` | Needs a defined method |
| Per-dimension editorial note | LLM-generated or operator-curated note per dimension; new store/field | Cost + curation question |
| Change meta deltas (market quotes) | A market-data source/feed feeding change records | New source — `augur-sources.md` revision |
| "23 edges" downstream impact | Compute affected-neighborhood size per change | Moderate graph query |
| Topic "weight" / attention rank | Define an attention metric over topic state/activity | Needs a definition |
| City-precise "your latitude" | Client geocoder, or finer `region_scope_definitions` | Geolocation precision question |
| Hyper-local change items | Sub-regional graph coverage (likely out of reach near-term) | Data-availability question |
| Scrubber event markers | A "notable events" table/endpoint | Small, self-contained |
| "14,402 sources nominal" | Real enabled-source count via `source_registry` | Trivial; real number ≈ 28 |
| Latin binomials | Static decorative labels (no data needed) | Cosmetic |
| Operator id / subscription / edition / build / hash | **Conflicts with `augur-presentation.md` line 106** and single-operator design | **Documents-first required** before building |
| Confidence regime label | Derive from spread of dimension directions | Needs a definition |

---

## Revisit triggers

- If the build chooses to **replace** `static/index.html` rather than run in parallel, confirm the current frontend's routes/consumers are migrated first.
- If geolocation scoping proves "too coarse to be useful" (the exact revisit trigger in `augur-presentation.md` line 127), that informs the city-precision backlog item — not a reason to add preference settings.
- Any §B item flagged **documents-first** pauses for a doc update before code, per `AGENTS.md`.
