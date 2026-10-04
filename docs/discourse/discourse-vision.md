# Augur Discourse — Vision

*What Augur Discourse is, why it exists beside Augur Core rather than inside it, and the principles that govern it. Discourse measures what is being talked about; Core models what is happening. This document is the root of the Discourse document set, the way `augur-vision.md` is the root of Core's.*

> **Staging note.** This document lives in the Augur Core repository only until the dedicated `augur-discourse` repository exists. When that repository is created, this file and `discourse-implementation-plan.md` move there and are deleted here.

---

## Reading guide

Cross-references:
- Augur Core's vision (whose principles Discourse inherits where they apply, and inverts where they don't) → `../augur-vision.md`
- Why Core excludes social media (the decision Discourse deliberately does not revisit) → `../augur-sources.md`, "Design decision: social media exclusion"
- Core's graph vocabulary (which Discourse reads but never writes) → `../augur-graph-schema.md`
- Build sequence for Discourse → `discourse-implementation-plan.md`

---

## What Discourse is, in one paragraph

Augur Discourse is a measurement instrument for the information environment. It observes what people search for, watch, post and publish across short-form video, social platforms, search engines and news. It groups that material into topics, narratives and frames, measures how attention moves across platforms, countries and languages over time, and records any measurable signs of coordination. It runs beside Augur Core, reads Core's picture of what is actually happening, and shows the gap between the two. It does not decide what is true. Core does that work. Discourse measures who is saying what, how loudly, how fast and in which framing.

---

## The problem Discourse addresses

Core answers *"what is happening, and why?"* It does that by excluding most of the noise of public conversation: the reprints, reactions, viral cascades and engagement-optimized framing. That exclusion is correct for Core, and it stays.

But the noise is itself a fact about the world. What a population talks about, and how it talks about it, shapes politics, protest, trust and policy. And nobody sees it directly. Each person sees one algorithmically selected feed and infers from it what "everyone" is talking about. Someone who notices fuel-price anger on TikTok cannot tell whether that is a big phenomenon, a small one their feed happens to serve them, an organic one, or an amplified one.

The bottleneck here mirrors Core's. Core's bottleneck is that causal chains are *knowable but not legible*. Discourse's bottleneck is that the shape of public attention is *observable but not visible*: the data exists in fragments across platforms, but no individual can see the whole.

Discourse exists to make the shape of public attention visible, and to set it against what is actually happening.

---

## What Discourse is not

Stated explicitly because each of these is a tempting direction:

- **Not a propaganda detector.** Discourse never labels content as propaganda, disinformation or extremist as an output of its own analysis. Truth assessment belongs to Core. Discourse measures distribution, framing and coordination.
- **Not a judge of political positions.** No narrative is scored as legitimate or illegitimate. A narrative that aligns with a foreign state's interests is not thereby a foreign narrative.
- **Not a persuasion, targeting or recommendation tool.** Discourse produces no functionality for influencing audiences, targeting segments, or optimizing messaging. This holds even if it would be technically trivial.
- **Not social listening for marketing.** Discourse does not track brands, products or individuals' sentiment for commercial ends.
- **Not a surveillance tool.** Discourse analyzes aggregate patterns. It does not build profiles of private individuals (see principle 8).
- **Not a trending feed.** The dashboard shows attention as a measurement with coverage caveats, not as a stream to scroll.
- **Not part of Core's graph.** Nothing Discourse produces enters Core's signal pipeline, Tier A, or graph. Ever. See "Relationship to Augur Core."

---

## Relationship to Augur Core

Discourse and Core are **sibling systems with a one-way bridge**.

```
   ┌───────────────────────────┐            ┌───────────────────────────┐
   │        AUGUR CORE         │            │      AUGUR DISCOURSE      │
   │                           │            │                           │
   │  curated sources          │            │  broad platform sampling  │
   │  lens extraction          │            │  topic / narrative / frame│
   │  causal graph             │  read-only │  attention series         │
   │  "what is happening"      │ ─────────▶ │  coordination indicators  │
   │                           │   bridge   │  "what is talked about"   │
   │  never reads Discourse    │            │                           │
   └───────────────────────────┘            └───────────────────────────┘
                 │                                        │
                 └───────── shared infrastructure ────────┘
                  VPS · PostgreSQL instance · Langfuse · OpenRouter
```

The bridge carries Core's vocabulary (topics, entity aliases) and Core's picture of the world (events, conditions with history, quantities with time series) into Discourse, read-only and `as_of`-scoped. Nothing flows the other way.

### Why this protects Core

Core's hard commitments stay intact without exception. The social-media exclusion stands, the source registry is untouched, the graph schema doesn't change, and Tier A keeps its assumption that signals are independent. Discourse's material is exactly the non-independent echo that Core's convergence detection must never see. Keeping it in a separate system with no write path into Core is the structural guarantee that it never contaminates Core.

### The rule that holds the bridge together

**Attention never corroborates facts.** That fuel prices trend heavily on short-form video is evidence about *the discourse on fuel prices*, never about *fuel prices*. Discourse may display Core's values next to its own measurements, but no Discourse measurement is ever presented as evidence for or against a Core claim. A human operator who sees something in Discourse and suspects Core is missing a signal goes and finds that signal in Core's own admitted sources.

#### Design decision: separate repository

The alternative considered was a new package inside the Core repository (`src/augur/discourse/`), sharing Core's roadmap and CI.

The case for a separate repository:
- Core's roadmap and phase discipline stay clean. Discourse work never shows up as out-of-phase work in Core.
- Discourse has a different source philosophy, data volume profile, and dependency set (platform APIs, near-duplicate hashing). Keeping them apart prevents dependency creep into Core.
- The boundary is enforced by the repository, not by convention.

The case for a shared repository:
- One CI, one deployment, one place to read code.
- Easier reuse of Core's LLM client, logging, and config patterns.

**Current decision: separate repository, shared infrastructure.** Patterns from Core (the LLM client with Langfuse tracing, structured logging, budget enforcement) are copied deliberately rather than imported, so the two systems can evolve independently.

**Revisit trigger:** if the copied infrastructure code diverges painfully (for example, the same LLM-client bug fixed twice), extract a small shared library. Do not merge the repositories.

#### Design decision: same PostgreSQL instance, separate schema

The alternative considered was a separate database (or a separate PostgreSQL instance).

The case for the same instance with a `discourse` schema:
- Consistent with Core's one-PostgreSQL commitment (`../augur-architecture.md`). No new database technology, no new backup routine.
- The bridge becomes simple SQL against read-only views instead of a network API with its own failure modes.
- `as_of` scoping of bridge reads is trivial in SQL.

The case for a separate database:
- A harder boundary. A misconfigured grant cannot leak writes into Core tables.
- Independent resource limits. A Discourse ingestion spike cannot slow Core.

**Current decision: same instance, separate `discourse` schema.** Discourse connects as its own database role. That role owns the `discourse` schema and has `SELECT` only on a dedicated `core_bridge` schema of views that Core owns and publishes. It has no grants on any Core table.

**Revisit trigger:** if Discourse load measurably degrades Core (query latency, disk, vacuum pressure), move Discourse to a separate database and turn the bridge views into a small read-only API.

#### Design decision: shared vocabulary, read-only

The alternative considered was an independent Discourse taxonomy.

**Current decision: Discourse reads Core's alias table and topics through the bridge**, so that "bensinpris", "dieselpris" and "fuel prices" resolve the same way in both systems. Discourse keeps a supplementary lexicon of its own for discourse-specific vocabulary (hashtags, slang, platform-native terms) that Core has no reason to know. The Discourse lexicon never writes back to Core's alias table.

**Revisit trigger:** if Core's vocabulary proves systematically too formal to match how topics are actually discussed, so that most Discourse topic resolution falls through to its own lexicon anyway, drop the shared vocabulary and keep only an explicit mapping table between Discourse topics and Core topics.

---

## Core design principles

Ten commitments. Several are inherited from Core unchanged; several are deliberate inversions of Core, because Discourse studies the thing Core filters out.

### 1. Measurement, not judgment

Discourse reports what was observed and how it moved. It does not conclude what is true, who is right, or what is legitimate. Every analytical statement is phrased as an observation about the dataset: *"within the observed dataset, narrative X grew by Y% across these platforms and countries,"* never *"Europe thinks X."*

### 2. Echo is the measurement *(inverts Core)*

Core deduplicates reprints because they fake corroboration. Discourse counts them, because reach and repetition are the phenomenon. The same material is noise to Core and signal to Discourse.

### 3. Exposure over credibility *(inverts Core)*

Core weights sources by reliability. Discourse weights observations by reach: views, impressions, search volume, audience size. A low-credibility account with high reach matters more to the shape of attention than a high-credibility one nobody sees. Credibility is not ignored. It is simply not Discourse's question.

### 4. Platforms stay separate before they are combined

Every measurement exists per platform first. Cross-platform figures are computed only from platform-level ones, never by pooling raw material. Platforms differ in scale, audience and mechanics, so a raw sum across them is meaningless.

### 5. Coverage is a first-class output

What Discourse cannot see is shown as prominently as what it can. Every view carries its coverage: which platforms, countries and languages are observed, what is sampled, what is missing, which APIs limit what. A measurement without its coverage is not presented.

### 6. Observation, inference and attribution are different objects

Discourse keeps a strict ladder and never collapses its rungs:

| Rung | Meaning | Example |
|---|---|---|
| A | Observed narrative | Narrative X appears in the dataset |
| B | Coordinated distribution | Measurable coordination indicators present |
| C | Inauthentic behaviour | Accounts misrepresenting who they are, documented |
| D | Documented foreign involvement | A cited external investigation links activity to a foreign actor |
| E | Attribution to a specific state | A cited, authoritative attribution to a named state actor |

Rungs A–B are computed by Discourse. Rungs C–E are **only** recorded from cited external sources (EEAS, EUvsDisinfo, EDMO, DFRLab, platform transparency reports, peer-reviewed research). Discourse never infers C–E from patterns alone. A "Russia-favourable" narrative is rung A, nothing more.

### 7. No personalization *(inherited from Core)*

Every viewer sees the same picture. Discourse is an un-personalized view of attention: the antidote to the single-feed bias that motivated it. No preferences, no saved topics, no recommendations.

### 8. Aggregate, not individual

Discourse analyzes patterns, not people. Account identifiers are stored pseudonymized. Private individuals are never subjects of a view. Public figures, institutions, media outlets and documented campaign infrastructure may be named, because their public communication is the object of study. Raw content has a bounded retention period; aggregates are kept.

### 9. Time is first-class *(inherited from Core)*

Every observation carries the time the content was published, separate from when it was collected. Replay works the same way it does in Core: any view can be rendered `as_of` a past moment. Lead/lag analysis against Core depends on this.

### 10. The deterministic core runs without LLMs *(inherited and sharpened)*

Collection, topic resolution, attention metrics and coordination indicators are deterministic code. LLMs assist with narrative and frame extraction, where semantic interpretation genuinely adds value. Every LLM call goes through OpenRouter and emits a Langfuse trace, as in Core. If the LLM layer is switched off, the dashboard still shows attention and coordination. It just loses the narrative labels.

---

## Vocabulary

As in Core, code uses these words exactly.

- **Observation** — one collected item from one platform: a video, post, article, search-interest datapoint, or trend entry. Immutable once stored. Carries `content_timestamp`, `collected_at`, platform, country, language, source identifier, and engagement/exposure metrics where available.
- **Platform** — an observation surface: `tiktok`, `youtube`, `youtube_shorts`, `instagram`, `x`, `reddit`, `bluesky`, `mastodon`, `telegram`, `google_search`, `news`.
- **Topic** — what is being talked about ("fuel prices", "Norwegian electricity prices"). Resolved through Core's vocabulary plus Discourse's own lexicon. A topic carries no stance.
- **Narrative** — a recurring claim structure about a topic ("politicians are responsible for fuel prices"). Represented as a cluster of extracted claims. One topic has many narratives.
- **Frame** — the part of a causal story a narrative emphasizes or omits. Where Core holds a causal chain for the topic, a frame is expressed against that chain (for example, emphasizing the tax link while omitting the crude-price link).
- **Attention series** — a time series of exposure-weighted volume for a topic or narrative, per platform, country and language.
- **Propagation path** — the observed order in which a narrative appears across platforms, languages and actor types.
- **Coordination indicator** — one independently recorded, measurable sign of coordinated distribution (synchronized posting, identical text, reused media, shared domains). Never merged into a single score.
- **Attribution record** — a cited external claim at rungs C–E linking activity to an actor. Always carries its citation.
- **Coverage report** — what a given view could and could not see.

---

## Layers of the information environment

Discourse observes five layers, kept apart because they answer different questions:

| Layer | Question it answers | Example sources |
|---|---|---|
| Search | What are people actively looking for? | Google Trends, YouTube search trends |
| Short-form video | What are platforms showing people? | YouTube Shorts, TikTok, Reels (as access permits) |
| Social text | What are people saying and sharing? | Bluesky, Mastodon, Reddit, X, public Telegram channels |
| News | What are journalists publishing? | National and international press (via Core's payloads where possible) |
| Research & FIMI | What have monitors and investigators documented? | EDMO, EEAS / EUvsDisinfo, DFRLab, platform transparency reports |

Short-form video is the layer of greatest interest and the hardest to reach. Discourse is designed so that it works usefully on the open layers first and gains short-form depth as access becomes available. It never pretends to coverage it does not have (principle 5).

---

## Seeing trends across Discourse and Core

This is where the pair becomes more than the sum of its parts. Neither system alone can tell you that a conversation is running ahead of reality, or that reality is moving with nobody watching. Together they can.

The cross-system analysis is built up gradually, in order of increasing ambition. Every step depends on enough history in both systems to have stable baselines.

### Stage 1 — Side by side

The simplest form: a Discourse attention series plotted on the same time axis as a Core quantity or condition. Discourse volume on Norwegian electricity prices next to the NO1–NO5 spot price. Fuel-price discourse next to Brent and pump prices. Nothing is computed; the human eye does the work. This is useful from the first week and carries no analytical risk.

### Stage 2 — Event windows

Core events become anchors. For each significant Core `Event` (for example a Hormuz disruption or a central bank decision), Discourse renders a standard window: **T−30 days → T0 → T+7 → T+30**, for every topic linked to that event through the shared vocabulary. This answers:

- Did discourse on the topic exist **before** the event, or appear only after it?
- How quickly did attention peak, and on which platform first?
- Which narratives appeared in the window, and in what order?
- Did any coordination indicators or attribution records appear, and when relative to the event?

The order of events is the core of this analysis. *Event → reporting → narrative → amplification* is a different phenomenon from *narrative → event interpretation*, and only aligned timelines can tell them apart.

### Stage 3 — Divergence patterns

Once baselines exist, Discourse computes recurring patterns between attention and reality. Each pattern is a named, documented computation, not an LLM judgment:

- **Over-attention** — attention well above its own baseline while the linked Core quantity or condition is at or near *its* baseline. The conversation is louder than the underlying change.
- **Under-attention** — a Core condition activates or a quantity moves well outside its range while attention stays at baseline. Something real is happening that almost nobody is discussing. This is Core's "knowable but not legible" problem, measured directly, and often the most valuable signal the pair produces.
- **Narrative persistence** — attention stays elevated after the linked Core condition has deactivated. The story outlives its cause.
- **Lead / lag** — discourse systematically precedes or follows movement in a Core quantity, estimated with cross-correlation at offsets. Discourse that leads is a different phenomenon from discourse that follows.
- **Frame share vs causal share** — for topics where Core holds a causal chain (for example crude → refining → margins → taxes → pump price), the share of discourse that emphasizes each link compared with the link's weight in Core's graph. Discourse that is 80% about taxes, for a price move Core attributes mainly to crude, is a frame gap. Core's weight bands are ordinal, so the comparison is ordinal too: "the most-discussed link is Core's weakest" rather than a ratio.

### Stage 4 — Narrative transfer

The most ambitious analysis, and the one the original conversation pointed toward: following whether a narrative structure established around one topic later appears around others. For example, a sequence where blame framing appears first around the Iran war, then around fuel prices, then around electricity prices, then as general distrust of politicians. Discourse detects this as narrative clusters with high semantic similarity appearing across topics in temporal sequence. Core's causal graph tells you whether a real causal path connects those topics (Iran war → oil → fuel prices). The interesting cases are where narrative transfer follows Core's causal path, and especially where it *doesn't*.

### Guardrails for all cross-system analysis

- **Correlation is described, never asserted as cause.** "Attention rose 4 days after the price move" is a finding. "The price move caused the anger" is not.
- **Core is never modified by these analyses.** Divergence findings are stored in Discourse's own schema. They never become signals, claims or edges in Core.
- **Core values are always shown with provenance.** A Core quantity shown in Discourse links back to the Core reasoning view, so the reader can see why Core believes it.
- **Baselines are explicit.** Every "above baseline" states the baseline window and method. Divergence patterns are not computed until enough history exists. The implementation plan sets the minimum.
- **Coverage applies doubly.** A divergence finding inherits the coverage limits of both systems. "Under-attention" may mean the conversation is happening on a platform Discourse cannot see. The coverage report says so.

---

## Who Discourse is for

The same audience as Core: the operator first, then a small number of people who think in systems and want to inspect reasoning rather than receive conclusions. In particular, someone who wants to check whether what their own feed shows them reflects the wider information environment.

---

## Failure modes

Ways Discourse could fail, roughly in order of likelihood:

1. **Mistaking coverage for reality.** The dataset over-represents the open platforms (Bluesky, Mastodon, YouTube) and under-represents the closed ones (TikTok, Instagram), and the dashboard is read as if it showed "social media." Principle 5 exists to prevent this.
2. **Drifting into judgment.** Narrative labels gradually become verdicts: "the Russian narrative," "the disinformation cluster." The attribution ladder exists to prevent this.
3. **Pattern-to-story inflation.** Coordination indicators are found, and an LLM or a human tells a convincing story about who did it. Rungs C–E only from citations.
4. **Leaking into Core.** Pressure to "just use" Discourse findings as Core signal. The one-way bridge exists to prevent this.
5. **Collection becoming the project.** Platform access is hard and changes constantly. Time spent fighting APIs crowds out analysis. Start with open data; add closed platforms only when access is stable.
6. **Becoming interesting rather than honest.** As with Core, a dashboard full of dramatic spikes is more engaging than one showing coverage gaps. Honesty wins.

---

## What Discourse cannot do

- It cannot see private conversation: messaging apps, closed groups, private accounts. A large share of real discourse is invisible to it.
- It cannot see what any given person is shown. Platform algorithms personalize. Discourse observes public aggregate signals, not individual feeds.
- It cannot measure persuasion. Exposure is not belief; engagement is not agreement.
- It cannot attribute campaigns on its own. It can surface coordination indicators and record attributions others have published.
- It cannot be neutral. Platform selection, country and language selection, and the topic lexicon all reflect choices. As in Core, they are named rather than hidden.

---

## Revision posture

This document changes before the code that motivates a change, as in Core. Stable parts: the one-way bridge, the rule that attention never corroborates facts, the attribution ladder, no personalization, and the not-a-targeting-tool commitment. Changing any of these requires an explicit conversation with the operator. Everything else, including the platform list, the layers, and the divergence pattern catalog, is expected to evolve with use.
