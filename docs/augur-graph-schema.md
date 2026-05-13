# Augur — Graph Schema

*The structure of what Augur believes about the world. Defines node types, edge types, weight semantics, the proposed_anchors schema, and a worked seed graph. Inherits from `augur-vision.md` and `augur-architecture.md`.*

---

## Reading guide

This document is the source of truth for graph structure. When code, prompts, or other documents reference a node type or edge type, the definitions here are canonical.

Cross-references:
- Vision (why the graph exists at all) → `augur-vision.md`
- Architecture (where the graph sits in the system) → `augur-architecture.md`
- Lens extraction (what produces proposed_anchors) → `augur-signal-pipeline.md`
- Source provenance (where signals come from) → `augur-sources.md`

---

## Design principles for the schema

Six commitments that shape every decision in this document:

1. **Small fixed type system.** A constrained taxonomy of nodes and edges, not a free-for-all. Adding a new type is a deliberate schema change, not something an LLM does at extraction time.
2. **Causal direction is explicit.** Edges are directed. *A causes B* is structurally different from *B causes A* and from *A correlates with B*. The schema enforces this distinction.
3. **Weights are ordinal, not probabilities.** A weight of 0.8 means "strongly supported," not "80% probability." This is enforced by using a small set of named weight bands rather than continuous values.
4. **Every claim cites its sources.** Nodes and edges carry references to the signals that established them and the signals that maintain them. An unsupported edge is invalid by construction.
5. **Time is first-class.** Conditions activate and deactivate. Edges strengthen and weaken. The schema records this as time-series history, not as overwrites.
6. **Aliases and entity resolution are owned by the schema, not by extraction.** "United States," "USA," "US," and "the U.S." resolve to one canonical node. The extraction layer proposes; the schema's entity resolver decides.

---

## Node types

Augur has **six node types**. No more without a schema revision.

### `Entity`

A persistent actor in the world. Things that have identity over time and can be referred to repeatedly across years.

**Examples:** "United States," "OPEC," "BHP Group," "Bank of Japan," "Strait of Hormuz," "Morocco's phosphate sector."

**Required fields:**
- `name` (canonical)
- `aliases` (list of alternate names)
- `entity_kind` (one of: `state`, `organization`, `company`, `place`, `infrastructure`, `sector`, `commodity`, `currency`, `instrument`)
- `description` (one paragraph)
- `created_from` (signal IDs that established this node)
- `created_at`, `updated_at`

**Identity rule:** entities are immutable in identity. If "Twitter" becomes "X," it's still the same entity with a new name and a logged transition. Renaming does not create a new node.

### `Condition`

A persistent state of the world that is currently true or false (or somewhere in between). Conditions activate and deactivate over time.

**Examples:** "Iranian crude exports through Hormuz are constrained," "Global fertilizer prices are elevated," "US-China semiconductor trade restrictions in force," "Norwegian wholesale electricity prices above 2025 average."

**Required fields:**
- `name` (concise statement of the condition)
- `description` (one paragraph elaborating)
- `current_state` (one of: `active`, `inactive`, `partially_active`, `disputed`, `unknown`)
- `current_state_confidence` (weight band; see below)
- `activation_history` (time series of state changes)
- `subject_entities` (list of entity node IDs this condition is about)
- `created_from`, `created_at`, `updated_at`

**Identity rule:** conditions are about a thing being in a state, not about a specific event. "Iran-Israel conflict ongoing" is a condition. "Iran-Israel conflict began on February 28, 2026" is an event.

### `Event`

A discrete occurrence at a specific moment (or short window) in time. Events do not persist; they happened and then the effects propagate.

**Examples:** "Iran-Israel war began (Feb 28, 2026)," "Federal Reserve cut rates by 25bp (Apr 2026)," "M7.4 earthquake near Bandar Abbas (Mar 15, 2026)," "Polish central bank announced 700t gold target (Jan 2026)."

**Required fields:**
- `name`
- `description`
- `occurred_at` (timestamp or date range)
- `occurred_location` (geographic reference; optional, PostGIS)
- `event_kind` (one of: `geopolitical`, `economic`, `physical`, `policy`, `corporate`, `natural`)
- `subject_entities`
- `created_from`, `created_at`

**Identity rule:** events are immutable once recorded. New evidence can adjust supporting signals, but the event itself is a historical record.

### `Quantity`

A numeric or quasi-numeric measure tracked over time. Where conditions are qualitative, quantities are values.

**Examples:** "WTI crude oil price (USD/bbl)," "Hormuz tanker transit count (daily)," "Polish central bank gold reserves (tonnes)," "Brent-Dubai spread (USD/bbl)," "Baltic Dry Index."

**Required fields:**
- `name`
- `unit` (string)
- `time_series_reference` (pointer to time-series table, or external API spec)
- `current_value`, `current_value_as_of`
- `subject_entities`
- `created_from`, `created_at`

**Identity rule:** quantities have a single canonical definition. Different methodologies for measuring the same conceptual thing are different quantities (e.g., "Chinese gold reserves (official PBOC)" and "Chinese gold reserves (analyst estimate)" are separate quantity nodes whose discrepancy is itself signal).

### `Scenario`

A named hypothetical or projected future. Scenarios are user-created or projection-created, not extracted from sources.

**Examples:** "Iran conflict de-escalates by Q3 2026," "Fertilizer supply normalizes within 18 months," "EU electricity prices remain elevated through winter 2026-27."

**Required fields:**
- `name`
- `description`
- `precondition_nodes` (list of nodes whose state is assumed)
- `projected_trajectory` (description of how the scenario unfolds)
- `created_by` (user ID or `projection_engine`)
- `created_at`

**Identity rule:** scenarios are saved hypotheticals. They reference graph state at creation time so they can be revisited later.

### `Claim`

A specific assertion in the world that may be true or false, and that Augur tracks the evidence around. Claims sit between conditions and events: they are assertions of fact that may be contested.

**Examples:** "China holds more gold than officially reported," "Russia is selling reserve gold to fund deficit spending," "European phosphate stocks are below five-year average."

**Required fields:**
- `text` (the claim as a statement)
- `claim_kind` (one of: `factual`, `interpretive`, `contested`)
- `evidence_for` (signal IDs)
- `evidence_against` (signal IDs)
- `current_assessment` (one of: `well_supported`, `partially_supported`, `contested`, `weakly_supported`, `not_supported`)
- `subject_entities`
- `created_from`, `created_at`, `updated_at`

**Why this is separate from condition:** conditions are states of the world that Augur treats as established enough to reason from. Claims are propositions that are still being evaluated. A claim can be promoted to a condition once the evidence consolidates.

---

## Edge types

Augur has **nine edge types**. No more without a schema revision.

All edges are directed. All edges carry a weight band and supporting signal references.

### Causal edges

#### `causes`
*A causes B.* The strongest form: A's occurrence or persistence directly produces B.

#### `enables`
*A enables B.* A is a necessary precondition for B, but not by itself sufficient. Removing A makes B impossible or much harder.

#### `constrains`
*A constrains B.* A limits the magnitude, scope, or likelihood of B. Negative-direction influence.

#### `accelerates`
*A accelerates B.* A makes B happen sooner or more intensely than it otherwise would.

### Relational edges

#### `correlates_with`
*A correlates with B.* The two co-vary, with no claim about causal direction. Used when a relationship is observable but the mechanism is unclear or contested.

#### `contradicts`
*A contradicts B.* The two cannot both be true (or both be active) simultaneously, or their joint presence is strong evidence that one is mismeasured.

#### `refines`
*A refines B.* A is a more specific or nuanced version of B. Used to link narrower claims to broader ones.

### Structural edges

#### `part_of`
*A is part of B.* Hierarchical containment. "Strait of Hormuz" is part of "Persian Gulf shipping." "Phosphate" is part of "Fertilizer inputs."

#### `produces`
*A produces B.* Used specifically for quantity-producing relationships. "Morocco produces global phosphate supply." Distinct from `causes` in that it's about ongoing production rather than triggering a state change.

### Required fields on every edge

- `source_node` (FK)
- `target_node` (FK)
- `edge_type` (one of the nine above)
- `weight_band` (see weight semantics below)
- `weight_history` (time series of weight changes)
- `supporting_signals` (list of signal IDs)
- `disconfirming_signals` (list of signal IDs, for edges that have been challenged)
- `reasoning` (free-text explanation of why this edge exists)
- `falsification_criteria` (what would weaken or invalidate this edge — required field, not optional)
- `created_from`, `created_at`, `updated_at`
- `last_disconfirmation_pass` (timestamp; null if never challenged)

The `falsification_criteria` requirement is structural. An edge cannot be created without specifying what would weaken it. This forces the discipline at edge-creation time and makes the disconfirmation pass tractable.

---

## Weight semantics

Edge weights are **ordinal qualitative bands**, not continuous probabilities.

The bands:

| Band name | Numeric anchor | Meaning |
|---|---|---|
| `strong` | 0.8 | Multiple independent signals across multiple perspectives, hard physical or economic data, broad consensus among observers. |
| `moderate` | 0.6 | Several signals, generally consistent, but some interpretive distance or single-perspective dominance. |
| `weak` | 0.4 | Some evidence, but contested or thin, or relies on inference chains rather than direct observation. |
| `provisional` | 0.2 | Plausible based on theoretical or analogical reasoning, but minimal direct evidence yet. |
| `disputed` | (no anchor) | Evidence pulls in conflicting directions; edge is recorded but explicitly flagged as unresolved. |

**The numeric anchors exist only for projection-time arithmetic** (multiplying chained weights along a path). They are not probabilities and should never be reported to users as probabilities. The user-facing representation is always the band name.

**Weight history is append-only.** When an edge's weight band changes, the change is appended to `weight_history` with timestamp, triggering signal IDs, and a reasoning string. Past weights are never overwritten.

**Why bands rather than continuous weights:** continuous weights invite false precision. A 0.73 weight implies measurement that doesn't exist. Bands force the operator and the LLM to assign edges to discrete categories, which is both more honest and more navigable when reading the graph.

**Conversion to action:** when projection produces a multi-hop path with bands `strong → moderate → weak`, the output communicates this as "this trajectory has one strong link, one moderate, and one weak — its fragility is at the weak link" rather than as "this trajectory has probability 0.192."

---

## The proposed_anchors schema

This is the structured output that the **lens extraction layer** produces, consumed by the **anchoring layer** (see `augur-architecture.md` for the flow). It is the contract between extraction and anchoring.

A single signal can propose zero or more anchors. Each anchor is one of the following operations.

### `create_node`

```yaml
operation: create_node
node_type: Entity | Condition | Event | Quantity | Claim
proposed_id: <slug, e.g., "iran_israel_war_2026">
fields:
  name: "..."
  description: "..."
  # plus type-specific required fields
reasoning: "Why this node is being proposed."
```

### `update_node`

```yaml
operation: update_node
target_node_id: <existing node ID>
field_updates:
  current_state: active  # or any updateable field
reasoning: "..."
```

### `create_edge`

```yaml
operation: create_edge
source_node_id: <ID or proposed_id from same batch>
target_node_id: <ID or proposed_id from same batch>
edge_type: causes | enables | constrains | ...
proposed_weight_band: strong | moderate | weak | provisional | disputed
reasoning: "..."
falsification_criteria: "What would weaken this edge."
```

### `update_edge_weight`

```yaml
operation: update_edge_weight
target_edge_id: <existing edge ID>
new_weight_band: strong | moderate | weak | provisional | disputed
direction: strengthen | weaken
reasoning: "..."
```

### `add_supporting_signal`

```yaml
operation: add_supporting_signal
target_edge_id: <existing edge ID>
signal_id: <the signal proposing this anchor>
```

### `add_disconfirming_signal`

```yaml
operation: add_disconfirming_signal
target_edge_id: <existing edge ID>
signal_id: <the signal proposing this anchor>
```

### Anchor validation

The anchoring applier enforces:

- `node_type` and `edge_type` must be in the allowed set.
- `weight_band` must be a named band.
- `falsification_criteria` is required on any `create_edge`. Missing → reject.
- A signal cannot propose more than ~10 anchors. More than that suggests the lens is over-extracting and the signal should be split.
- Anchors that reference proposed IDs from the same batch must form a valid DAG (no circular references at creation time).
- An anchor that would create a node duplicating an existing entity (after alias resolution) is rewritten to use the existing entity.

Rejected anchors are logged, not silently dropped. They are reviewable in the operator UI.

---

## Entity resolution and aliases

The hardest practical schema problem. The schema commits to:

- **A canonical name per entity, plus an `aliases` list.** "United States" is canonical; "USA," "US," "United States of America," "the U.S." are aliases.
- **Alias resolution is a deterministic lookup, not an LLM call.** A normalization pass at anchoring time runs the proposed name through a case-insensitive alias table. If a match exists, the anchor is rewritten to use the canonical node ID. If not, the anchor proposes a new entity.
- **Alias additions go through the same applier as any other graph mutation.** The LLM can propose adding an alias ("Sec. Yellen" is an alias for "Janet Yellen") but the applier confirms there's no ambiguity before applying.
- **Disambiguation requires explicit fields.** "Georgia" → country or US state? The schema disambiguates by requiring `entity_kind` (`state` vs `place`) and, where ambiguity is structural, by including disambiguation in the canonical name: "Georgia (country)" and "Georgia (US state)" are separate canonical names.
- **Historical renames are tracked.** When "Twitter" became "X," the entity node retains its identity but gains an alias and a `name_history` entry.

For the seed graph, the alias table starts with the major countries, currencies, central banks, and commodities — perhaps 200-300 entries hand-curated. Growth is incremental and operator-reviewed.

---

## Time and provenance

### Time on nodes

- `Entity` and `Quantity` nodes have `created_at` and `updated_at`. They do not have activation states.
- `Condition` nodes have `current_state` plus `activation_history` (append-only log of state transitions).
- `Event` nodes have `occurred_at` (single timestamp or range). Events are points in time, not durations.
- `Scenario` nodes have `created_at` and a `precondition_state_snapshot` describing what the graph looked like when the scenario was created.
- `Claim` nodes have `created_at`, `updated_at`, and `assessment_history` (append-only log of current_assessment changes).

### Time on edges

Every edge has `weight_history` as an append-only time series. The edge's "current" weight is just the most recent entry. This means:

- The graph at any point in the past can be reconstructed by replaying weight_history up to that timestamp.
- A user asking "what did Augur think in March about X→Y?" gets a real answer.
- Disconfirmation events leave durable traces in the weight history.

### Provenance

Every node and every edge carries `created_from` (the originating signal IDs) and is linked to:

- The originating raw payloads (via signals → payloads).
- The source IDs (via payloads → sources).
- The lens IDs that produced the signals.
- The Langfuse trace IDs for the LLM calls involved.

This is the chain that powers the "why does Augur think this?" trace from the architecture document.

---

## Worked example: an illustrative seed graph

The fertilizer→food chain. This is a **worked example used for schema documentation and design validation**, not the operational seed of the live system.

The operational seed graph is expected to emerge from retroactive calibration runs (see `augur-calibration.md`, written after the signal pipeline document) rather than from hand-authored bootstrapping. Hand-authored seed graphs encode the author's priors directly into the system and bypass the source-confidence calibration that gives the system its empirical grounding. Letting the seed emerge from actual signal extraction against historical sources is the more honest approach.

This worked example exists to:

1. Stress-test the schema by populating it with real-world content.
2. Document the canonical shape of nodes and edges for engineers and LLM extractors building against the schema.
3. Surface tensions in the schema that pure design wouldn't reveal (see "what this seed teaches" at the end).

The chain is small enough to hand-author, large enough to expose the schema's edges, and based on causal relationships that are reasonably well-supported by public evidence as of mid-2026.

### Entities (8)

```yaml
- id: iran
  name: "Iran"
  entity_kind: state

- id: israel
  name: "Israel"
  entity_kind: state

- id: hormuz
  name: "Strait of Hormuz"
  entity_kind: infrastructure

- id: morocco_phosphate
  name: "Morocco phosphate sector"
  entity_kind: sector

- id: global_fertilizer_market
  name: "Global fertilizer market"
  entity_kind: sector

- id: global_food_market
  name: "Global food market"
  entity_kind: sector

- id: low_income_food_importers
  name: "Low-income food-importing countries"
  entity_kind: sector

- id: global_migration_flows
  name: "Global migration flows (south-north)"
  entity_kind: sector
```

### Conditions (5)

```yaml
- id: iran_israel_conflict_active
  name: "Iran-Israel armed conflict ongoing"
  current_state: active
  subject_entities: [iran, israel]

- id: hormuz_shipping_disrupted
  name: "Hormuz shipping flow significantly disrupted"
  current_state: partially_active
  subject_entities: [hormuz]

- id: fertilizer_supply_constrained
  name: "Global fertilizer supply constrained vs 2024 baseline"
  current_state: active
  subject_entities: [global_fertilizer_market]

- id: food_prices_elevated
  name: "Global food prices elevated above 2024 baseline"
  current_state: partially_active
  subject_entities: [global_food_market]

- id: migration_pressure_elevated
  name: "Migration pressure from food-stressed regions elevated"
  current_state: unknown
  subject_entities: [low_income_food_importers, global_migration_flows]
```

### Events (1)

```yaml
- id: iran_israel_war_began_2026
  name: "Iran-Israel war began"
  occurred_at: 2026-02-28
  event_kind: geopolitical
  subject_entities: [iran, israel]
```

### Edges (7)

```yaml
- source: iran_israel_war_began_2026
  target: iran_israel_conflict_active
  edge_type: causes
  weight_band: strong
  reasoning: "The event initiated the ongoing condition."
  falsification_criteria: "Verified cessation of hostilities."

- source: iran_israel_conflict_active
  target: hormuz_shipping_disrupted
  edge_type: causes
  weight_band: moderate
  reasoning: "Regional conflict reduces tanker traffic and raises insurance premiums."
  falsification_criteria: "Tanker transit counts (AIS) returning to pre-conflict baseline."

- source: iran_israel_conflict_active
  target: fertilizer_supply_constrained
  edge_type: causes
  weight_band: moderate
  reasoning: "Energy and shipping disruption affect ammonia and phosphate production and distribution."
  falsification_criteria: "Fertilizer price indices and trade flow data returning to baseline despite continued conflict."

- source: hormuz_shipping_disrupted
  target: fertilizer_supply_constrained
  edge_type: enables
  weight_band: weak
  reasoning: "Shipping disruption is one of several pathways from conflict to fertilizer constraint."
  falsification_criteria: "Fertilizer supply normalizes while Hormuz remains disrupted."

- source: fertilizer_supply_constrained
  target: food_prices_elevated
  edge_type: causes
  weight_band: moderate
  reasoning: "Fertilizer cost and availability propagate to crop yields and grain markets, with a 6-12 month lag."
  falsification_criteria: "Wheat, maize, and rice futures remaining at baseline despite fertilizer constraint persisting."

- source: food_prices_elevated
  target: migration_pressure_elevated
  edge_type: accelerates
  weight_band: weak
  reasoning: "Food stress historically contributes to migration, especially from low-income importing regions."
  falsification_criteria: "Migration indicators (border encounters, asylum claims, IOM data) showing no correlation with food price elevation."

- source: morocco_phosphate
  target: global_fertilizer_market
  edge_type: produces
  weight_band: strong
  reasoning: "Morocco is the largest phosphate producer and reserve holder globally."
  falsification_criteria: "USGS or producer-reported production data showing Moroccan share has dropped materially."
```

### What this seed teaches

Building this by hand surfaces a few things the schema needs to handle that aren't obvious from pure design:

- The same outcome (fertilizer supply constrained) can have multiple incoming edges of different types and weights. The schema supports this — projection summing or chaining is the consumer's problem.
- Weight bands are unavoidable subjective judgments at the seed stage. The point is that they're *recorded* judgments with reasoning attached, not that they're correct.
- Falsification criteria are sometimes hard to write tightly. "Tanker transit counts returning to baseline" is good; "verified cessation of hostilities" is fuzzier. The discipline of writing them anyway is what matters.
- Some edges feel like they want to be conditional ("only when Hormuz is also disrupted"). The schema does not directly support conditional edges. The current answer is to add a separate condition node ("multi-vector fertilizer disruption active") that is itself causally connected — keeping the graph flat rather than introducing edge predicates.

---

## What the schema deliberately does not include

To keep the system buildable and the graph reasonable:

- **No conditional edges.** Edges fire when their source is active; they don't carry conditions of their own. If you need a conditional, model it as an intermediate condition node.
- **No probabilistic logic engine.** Weight bands are not Bayes nets. Projection multiplies weights as a heuristic, not as a calibrated probability.
- **No automatic ontology learning.** The node and edge type taxonomy is fixed by this document. The LLM cannot invent new types at extraction time.
- **No free-text predicates.** Edge types are the nine listed above. "Influences," "affects," "impacts," etc. are not edge types — they are vague placeholders that the schema rejects.
- **No multi-graph or sub-graph isolation.** One graph. Different subject areas live as connected subgraphs, not as separate graphs.
- **No private/secret nodes.** All graph content is visible to all readers of the graph. Privacy is enforced at the deployment level, not inside the schema.
- **No automatic node deletion.** Nodes can be marked deprecated (e.g., a defunct organization) but the schema retains them for historical traceability.

---

## Revision posture

This schema is the foundational vocabulary of the system. Changes to it require:

1. A version increment.
2. A migration plan for existing nodes and edges, if any structural fields change.
3. An update to `augur-signal-pipeline.md` to reflect lens output schema changes.
4. An update to the architecture's contract definitions if the proposed_anchors schema changes.

Adding a new node type, edge type, or weight band is allowed but should be rare. Most extensions can be accommodated by being more careful with the existing types. When in doubt, lean toward not adding.

The seed graph is illustrative, not authoritative or operational. Real graph content will emerge from calibration runs against historical sources and will evolve continuously thereafter. The worked example above exists to bootstrap *reasoning about the schema*, not to bootstrap the *content of the graph*.
