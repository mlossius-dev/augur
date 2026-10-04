# Augur Discourse — Implementation Plan

*The build sequence for Augur Discourse, from an empty repository to a working dashboard with cross-system analysis against Augur Core. Each phase has a clear success condition and an explicit list of what it does not include, following the pattern of Core's `augur-roadmap.md`. Inherits from `discourse-vision.md`.*

> **Staging note.** This document lives in the Augur Core repository only until the dedicated `augur-discourse` repository exists. It moves there along with `discourse-vision.md`.

---

## Reading guide

Cross-references:
- Why Discourse exists and the principles it must keep → `discourse-vision.md`
- Core's roadmap discipline, which this plan copies → `../augur-roadmap.md`
- Core's infrastructure, which Discourse shares → `../augur-architecture.md`
- Core's LLM client, logging and budget patterns, which Discourse copies → `src/augur/llm/`, `src/augur/logging.py`, `src/augur/config.py` in the Core repository

---

## Sequencing principles

1. **Open data before closed data.** Build the whole pipeline end to end on platforms with open, stable access. Add closed platforms (TikTok, Instagram, X) only once the pipeline is proven and access is secured.
2. **Deterministic before LLM.** Attention metrics and coordination indicators come before narrative extraction. The dashboard must be useful with the LLM layer switched off (vision, principle 10).
3. **Coverage from day one.** The coverage report is built in the same phase as the first collector, not added later.
4. **Side by side before analysis.** Show Discourse and Core next to each other before computing anything across them.
5. **Long-lead tasks start early.** Research API applications (TikTok under DSA Article 40, others) take months. Start them in D0 even though they pay off in D9.

---

## Architecture at a glance

```
  ┌───────────────────────────────────────────────────────────────────────┐
  │ COLLECTORS (one per platform, deterministic)                          │
  │ google_trends · youtube · bluesky · mastodon · telegram · reddit ·   │
  │ news (via core_bridge) · fimi_reports · [tiktok · instagram · x]      │
  └──────────────────────────────┬────────────────────────────────────────┘
                                 ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │ OBSERVATION STORE  (immutable, content_timestamp + collected_at)      │
  │ + collection_runs + coverage_snapshots                                │
  └──────────────────────────────┬────────────────────────────────────────┘
                                 ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │ TOPIC RESOLUTION  core aliases/topics (bridge) + discourse lexicon    │
  │                   + embedding clustering for unmatched material       │
  └──────────┬───────────────────────┬──────────────────────┬─────────────┘
             ▼                       ▼                      ▼
  ┌────────────────────┐  ┌────────────────────┐  ┌────────────────────────┐
  │ ATTENTION METRICS  │  │ NARRATIVES/FRAMES  │  │ COORDINATION INDICATORS│
  │ deterministic      │  │ LLM-assisted       │  │ deterministic          │
  └─────────┬──────────┘  └─────────┬──────────┘  └───────────┬────────────┘
            └───────────────────────┼─────────────────────────┘
                                    ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │ CROSS-SYSTEM ANALYSIS  event windows · divergence · lead/lag · frames │
  │ reads Core via core_bridge (read-only, as_of)                         │
  └──────────────────────────────┬────────────────────────────────────────┘
                                 ▼
  ┌───────────────────────────────────────────────────────────────────────┐
  │ API (FastAPI)  →  DASHBOARD                                           │
  └───────────────────────────────────────────────────────────────────────┘
```

Package layout in the new repository, one package per stage, the same way Core organizes its packages:

```
src/discourse/
  collectors/        one module per platform
  observations/      store, coverage
  bridge/            core_bridge reader (read-only, as_of)
  topics/            resolution, lexicon, clustering
  attention/         series, baselines, metrics
  narratives/        claim extraction, clustering, frames
  coordination/      indicators, attribution records
  crosssystem/       event windows, divergence patterns
  llm/               copied from Core: OpenRouter client + Langfuse + budget
  api/
  cli/
  db/migrations/
static/              dashboard
tests/
```

---

## Phase D0 — Repository, infrastructure and long-lead tasks

**Work:**
- Create the `augur-discourse` repository. Move `discourse-vision.md` and this plan into its `docs/`, and delete the staging copies from Core.
- Write `AGENTS.md` for the new repository, modelled on Core's: the hard commitments are the one-way bridge, attention never corroborates facts, the attribution ladder, no personalization, no targeting functionality, every LLM call traced, and `content_timestamp` everywhere.
- Docker Compose service that joins the existing VPS network and uses the shared PostgreSQL instance, Langfuse, and OpenRouter.
- Create the database role `discourse` that owns schema `discourse` and has no grants on Core's schemas.
- Copy Core's LLM client (OpenRouter + Langfuse trace + daily budget cap) with Discourse's own stage tags: `discourse_narrative`, `discourse_frame`.
- **Start long-lead applications:** TikTok Research API / DSA Article 40 researcher data access. Record status in `docs/discourse-access-status.md`.

**D0 succeeds when:**
- The service starts, connects as the `discourse` role, and fails cleanly when it tries to write outside its schema.
- A trivial LLM call appears in Langfuse with the correct stage tag and counts against the Discourse budget.
- Access applications are submitted.

**D0 does not include:** any collector, any Core-side change.

---

## Phase D1 — Observation store and coverage

**Work:** the data model. Core tables in schema `discourse`:

- `platforms` — the platform catalog with access method and known limitations (free text, shown in coverage).
- `collection_runs` — one row per collector run: platform, query/scope, started/finished, item count, errors, rate-limit hits. This is the raw material for coverage reports.
- `observations` — immutable. `observation_id`, `platform`, `collection_run_id`, `external_id`, `content_timestamp`, `collected_at`, `country`, `language`, `author_ref` (pseudonymized, see below), `author_kind` (`public_figure`, `institution`, `media`, `account`, `unknown`), `url`, `text` (title/caption/body), `media_hashes`, `exposure` (JSONB: views, likes, shares, search index, as available), `raw_payload` (JSONB). Unique on `(platform, external_id, content_timestamp)`.
- `observation_metric_snapshots` — engagement grows after collection, so later re-fetches append snapshots rather than overwriting.
- `coverage_snapshots` — per day × platform × country × language: what was collected, what was attempted, known gaps.

**Pseudonymization:** `author_ref` is a keyed hash (HMAC with a secret held outside the database) of the platform account ID. Accounts with `author_kind` `public_figure`, `institution` or `media` may additionally carry a display name. Private accounts never do.

**Retention:** raw `text` and `raw_payload` are kept for a bounded window (proposed 180 days; operator to confirm), then nulled. Aggregates, clusters and indicators are kept indefinitely.

**D1 succeeds when:** Pydantic models for every table exist; migrations apply cleanly; unit tests cover immutability (no update path for observations), the uniqueness constraint, pseudonymization, and retention nulling.

**D1 does not include:** collectors.

---

## Phase D2 — The Core bridge

The only phase that touches the Core repository, and it touches it narrowly.

**Core-side work (in the Core repository, documents first):**
1. Add a short "Sibling systems" section to `augur-architecture.md` describing the `core_bridge` schema as a published, read-only contract. This must be committed before the migration.
2. Core migration creating schema `core_bridge` with views only:
   - `core_bridge.aliases` — `alias_text`, `canonical_name`, `canonical_node_id`.
   - `core_bridge.topics` and `core_bridge.topic_nodes`.
   - `core_bridge.events` — event nodes: id, name, `occurred_at`, `event_kind`, subject entities, location.
   - `core_bridge.condition_history` — condition state transitions with `content_timestamp`.
   - `core_bridge.quantity_series` — time-series points for quantities and market instruments (today these live in payload metadata from the FRED/Yahoo clients; the view normalizes them to `series_key, label, unit, ts, value`).
   - `core_bridge.edges_summary` — source, target, edge type, current weight band, for frame-share analysis.
   - `core_bridge.payload_index` — news payload metadata only (source, perspective, language, title, `content_timestamp`, url), so Discourse can count news attention without fetching the same articles twice.
3. `GRANT USAGE ON SCHEMA core_bridge` and `SELECT` on its views to role `discourse`. Nothing else.

**Discourse-side work:** `bridge/` reader with every query taking `as_of`; Pydantic models for each view; a contract test that fails loudly if a bridge view changes shape.

**D2 succeeds when:** Discourse can read Core aliases, topics, events, condition history and quantity series as of any past timestamp. A test confirms that the `discourse` role cannot read any Core table directly, and cannot write anywhere in Core.

**D2 does not include:** any write path from Discourse into Core, or any Core feature that reads Discourse.

---

## Phase D3 — Open-data collectors

One collector at a time, each with its own coverage reporting and recorded fixtures.

**Order:**
1. **News via `core_bridge.payload_index`.** Free, already curated, and it gives the "what journalists publish" layer immediately.
2. **Google Trends.** Search-interest per topic per country. Note in coverage that values are relative (0–100), sampled, and come through an unofficial client. Store as observations with `exposure.search_index`.
3. **YouTube Data API.** Search and video metadata for topic queries per region and language; Shorts identified by duration and format metadata; view counts as exposure; periodic re-fetch for metric snapshots. Quota-limited, so query budgets are configured per topic.
4. **Bluesky.** Jetstream firehose filtered by language and topic lexicon. The most complete open social stream available.
5. **Mastodon.** Public timelines of a curated set of large European instances.
6. **Telegram public channels.** An operator-curated list of public channels (news, political, regional), read via the public channel interface. Curation is a deliberate operator act, as source admission is in Core.
7. **FIMI reports.** EUvsDisinfo database, EEAS threat reports, EDMO publications, DFRLab. Stored as observations on the `research_fimi` layer. The raw material for attribution records in D7.

**Initial scope:** countries NO, SE, DK, DE, FR, UK, PL; languages nb/nn, sv, da, de, fr, en, pl. Narrow on purpose (operator to confirm).

**D3 succeeds when:** each collector runs on a schedule, writes observations and collection runs, populates coverage snapshots, and has recorded-fixture tests. The coverage report for any day correctly shows which layers and platforms were and were not observed.

**D3 does not include:** Reddit and X (paid or restricted access, so they go to D9), TikTok and Instagram (D9), video transcription (D9).

---

## Phase D4 — Topic resolution

**Work:**
- **Lexicon matching:** deterministic matching of observation text against `core_bridge.aliases`, `core_bridge.topics`, and the Discourse lexicon (`topic_lexicon` table: term, language, topic, added_by). Multilingual by construction: "bensinpris", "dieselpris", "Spritpreise", "prix du carburant" and "fuel prices" all map to one topic.
- **Embedding clustering** (pgvector) of unmatched observations into candidate topics. Candidates are proposed to the operator through the CLI, not auto-adopted, mirroring Core's rule against auto-discovered sources.
- **Burst detection** on unmatched clusters, so new topics surface quickly.
- Tables: `discourse_topics` (with optional `core_topic_id` mapping), `observation_topics` (many-to-many, with match method and score).

**D4 succeeds when:** at least 70% of observations on the open platforms resolve to a topic (sampled operator review confirms precision), and new-topic candidates appear in the CLI within a day of emerging.

**D4 does not include:** narratives (D6).

---

## Phase D5 — Attention metrics

**Work:**
- `attention_series`: daily (and hourly for fast topics) exposure-weighted volume per topic × platform × country × language.
- Per-series baselines: rolling median and MAD over a configurable window (default 90 days). "Above baseline" is always relative to a series' *own* baseline. Platforms are never pooled raw (vision, principle 4).
- Metrics: absolute volume, relative growth, acceleration, persistence, platform spread, country spread, language spread.
- Cross-platform index computed only from per-platform normalized scores, with the contributing platforms listed.
- Minimum history: no baseline-dependent metric is shown until a series has 30 days of data. Before that, the dashboard shows raw volume with a "baseline building" label.

**D5 succeeds when:** the question *"what were the 50 fastest-growing topics in the observed dataset in the last 24 hours, per platform?"* is answered deterministically, reproducibly, and with coverage attached.

**D5 does not include:** any cross-system comparison (D8).

---

## Phase D6 — Narratives and frames

The first LLM-assisted phase.

**Work:**
- **Claim extraction:** a cheap model extracts zero or more short claim statements from an observation's text (stage tag `discourse_narrative`). The work is sampled: per topic per day, observations are selected by exposure-weighted stratified sampling across platforms and countries, capped by the daily budget. Not every observation is read.
- **Narrative clustering:** claims are embedded and clustered per topic. Each cluster becomes a narrative with representative examples, first/last observed, and platform, country and language distributions.
- **Cross-language matching:** narratives in different languages are compared in a shared embedding space. Whether two are the same narrative, a local adaptation, or different is recorded as a similarity score plus an operator-confirmable link. It is never assumed.
- **Frames:** for topics mapped to a Core topic whose causal chain exists in `core_bridge.edges_summary`, a mid-tier model labels which links of the chain a narrative emphasizes and which it omits (stage tag `discourse_frame`). The output is structured: a list of Core edge IDs marked `emphasized` or `absent`, never free text.
- Prompts are versioned; prompt ID, model, and observation IDs go into every Langfuse trace.
- Tests use recorded LLM fixtures, never mocks (as in Core).

**D6 succeeds when:** for any topic, the dashboard can show its distinct narratives with representative examples and their distribution. A sampled operator review finds clusters coherent. The system still runs, minus narratives, with the LLM layer switched off.

**D6 does not include:** narrative transfer across topics (D8, stage 4).

---

## Phase D7 — Coordination indicators and attribution records

**Work: deterministic indicators,** each stored as its own row in `coordination_indicators` (indicator type, observation set, parameters, strength, computed_at):
- Near-duplicate text across distinct accounts (MinHash/LSH).
- Synchronized posting: many distinct accounts posting near-identical material within a short window.
- Reused media: perceptual hashes of thumbnails and images across accounts and platforms.
- Shared URL/domain clusters, including look-alike domains of known media.
- Identical translations: the same text appearing in several languages at nearly the same time.
- Account-age clustering, where the platform exposes creation dates.

No composite score. The dashboard lists indicators side by side, each with its method documented (vision, principle 6).

**Work: attribution records (rungs C–E):**
- Created only from FIMI-layer observations or operator entry, and always with a citation (URL, publisher, date, quoted passage).
- Linked to narratives, domains, or indicator sets the citation actually covers.
- The CLI refuses an attribution record without a citation. Tests enforce this the way Core's applier tests enforce `falsification_criteria`.

**D7 succeeds when:** a narrative's detail view shows its coordination indicators and any cited attribution records as separate sections, and no part of the system can produce rungs C–E without a citation.

**D7 does not include:** any automated attribution inference.

---

## Phase D8 — Dashboard v1 and cross-system analysis

**Dashboard views:**
1. **Attention map** — topics by platform × country, current period vs baseline.
2. **Topic view** — attention series per platform, narratives, a frames panel (where Core has a chain), and a link to the Core topic.
3. **Narrative view** — examples, distribution, propagation path, coordination indicators, attribution records.
4. **Coverage panel** — always visible; every view links to its coverage report.

**Cross-system analysis,** implementing the stages in `discourse-vision.md`, "Seeing trends across Discourse and Core":
- **Stage 1, side by side:** Core quantity series and condition history overlaid on Discourse attention series for mapped topics. Built first.
- **Stage 2, event windows:** for each Core event, the T−30 → T0 → T+7 → T+30 window across all linked topics, with narrative first-appearance and indicator timing marked.
- **Stage 3, divergence patterns:** over-attention, under-attention, narrative persistence, lead/lag (cross-correlation at ±14 days), and frame share vs causal share. Each pattern is a documented computation in `crosssystem/`, stored in `divergence_findings` with inputs, parameters, and both systems' coverage at the time. Enabled per topic only when both series have at least 90 days of history.
- **Stage 4, narrative transfer:** deferred to after D9. It needs months of narrative history to mean anything.

**D8 succeeds when:** the operator can, for a recent Core event, open its event window and see in one place what Core recorded, what was discussed, on which platforms first, in which narratives, and with which coordination indicators. Every number traces back to observations and Core provenance.

**D8 does not include:** narrative transfer, notifications, or personalization (permanently excluded).

---

## Phase D9 — Short-form depth and closed platforms

Gated on access, not on time.

**Work, as access arrives:**
- **TikTok** via the Research API, if the DSA Article 40 application is granted. Video metadata, captions, hashtags, view counts.
- **Instagram/Reels** via Meta Content Library if access is granted.
- **Reddit and X** if the operator accepts the API costs.
- **Transcripts and OCR** for short-form video. Open question: Core's commitment is that all inference goes through OpenRouter and the VPS never runs models locally. Speech-to-text and OCR are inference. They must go through an external provider that fits that commitment, and the choice requires an explicit operator decision before any code.

**D9 succeeds when:** each newly added platform appears in coverage reports with its limitations, and short-form observations reach the same pipeline stages as text observations.

---

## Phase D10 — Narrative transfer

**Work:** detect high-similarity narrative clusters appearing across different topics in temporal sequence. Compare transfer paths with Core's causal paths between the same topics (via `core_bridge.edges_summary`). Highlight transfers that follow a Core causal path, and transfers that do not.

**D10 succeeds when:** a transfer chain can be shown with its evidence (clusters, similarity scores, timestamps) and the matching or missing Core causal path, phrased strictly as observation.

---

## Testing discipline

Mirrors Core's:
- **Bridge contract tests** are mandatory. Bridge drift is the Discourse equivalent of a silent applier failure.
- **Permission tests:** the `discourse` role cannot write to Core, and cannot read Core outside `core_bridge`.
- **Attribution-citation tests** are exhaustive. Rungs C–E without a citation must be impossible.
- **Collector tests** use recorded platform responses.
- **LLM tests** use recorded fixtures or real calls to cheap models, with caching. Never mocks.

---

## Cost discipline

- Discourse has its own daily LLM budget cap, enforced in its copied LLM client, separate from Core's budget.
- Claim extraction is sampled and capped (D6). Volume of observations never translates directly into volume of LLM calls.
- Collectors respect platform quotas. The YouTube quota is the binding constraint and is budgeted per topic.
- Transcription (D9) is the largest potential cost driver and is gated on an explicit operator decision.

---

## Approximate timeline

Part-time, one operator, similar pace to Core:

| Phase | Duration | Cumulative |
|---|---|---|
| D0 — Repo, infra, applications | 1 week | 1 week |
| D1 — Observation store | 1–2 weeks | 2–3 weeks |
| D2 — Core bridge | 1 week | 3–4 weeks |
| D3 — Open collectors | 3–4 weeks | 6–8 weeks |
| D4 — Topic resolution | 2 weeks | 8–10 weeks |
| D5 — Attention metrics | 2 weeks | 10–12 weeks |
| D6 — Narratives and frames | 3 weeks | 13–15 weeks |
| D7 — Coordination and attribution | 2–3 weeks | 15–18 weeks |
| D8 — Dashboard and cross-system | 3–4 weeks | 18–22 weeks |
| D9 — Short-form / closed platforms | access-gated | — |
| D10 — Narrative transfer | 2–3 weeks, after months of history | — |

Baselines need 30–90 days of collected history, so collectors should start running as early in D3 as possible, even while later phases are being built.

---

## Open questions for the operator

1. **LLM key and budget:** a third OpenRouter key for Discourse, or the main key with a separate budget cap? The proposal is a separate key, so costs are cleanly separated.
2. **Raw-content retention:** is 180 days acceptable for raw text and payloads before nulling?
3. **Initial country and language scope:** is NO, SE, DK, DE, FR, UK, PL right for the start?
4. **TikTok / DSA Article 40:** will you apply as an individual researcher? Eligibility usually requires an affiliation with a research organization. This may decide whether TikTok is reachable at all.
5. **Transcription provider:** which external service is acceptable for speech-to-text and OCR, given the no-local-inference commitment?
6. **Telegram channel list:** curated by you, or seeded from published research lists and then reviewed?
