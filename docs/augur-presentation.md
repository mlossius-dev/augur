# Augur — Presentation

*How a human experiences Augur. The interaction model, the zoom hierarchy from glance to detail, the time scrubber, the conversation layer, and what the system deliberately does not do. Inherits from `augur-vision.md`, `augur-architecture.md`, `augur-graph-schema.md`, and `augur-sources.md`.*

---

## Reading guide

This document defines the user-facing surfaces of Augur. It does not specify pixel layouts or component libraries — those are implementation choices. It specifies what the system answers, in what sequence, and what the experience of using it feels like.

Cross-references:
- The reasoning being presented → `augur-graph-schema.md`
- Replay mode that powers the time scrubber → `augur-architecture.md`
- What "perspective" means in the UI → `augur-sources.md`
- The vision the UI is built to serve → `augur-vision.md`

---

## The structural insight

Presentation is not a design question first. It is a *question-answering* question first.

The user comes to Augur with a sequence of questions, ordered from abstract to concrete:

1. **Is the world improving or worsening?**
2. **How fast?**
3. **What changed since I last looked?**
4. **What does that mean for me?**
5. **Why?**

These are not five separate products. They are **five zoom levels on the same graph state.** A well-designed interface answers question 1 in seconds, question 2 in the next moment, question 3 with a glance at a panel, question 4 by scoping the same view to the user's location, and question 5 by drilling into causal structure.

Everything in this document is in service of that zoom hierarchy.

---

## The five zoom levels

### Level 1 — The world's trajectory

The most abstract view. Answers *"is the world improving or worsening, and along what dimensions?"*

Five dimensions, fixed for now:

1. **Economic stability** — the state of capital markets, monetary systems, banking, employment, and macroeconomic fragility.
2. **Geopolitical tension** — the state of state-to-state relations, conflict, alliances, and diplomatic friction.
3. **Resource availability** — the state of energy, food, water, and critical materials supply chains.
4. **Environmental stress** — the state of climate, weather extremes, ecological pressure, and natural-disaster activity.
5. **Structural change** — the state of long-running shifts in technology, demographics, and institutional capacity.

Each dimension has a current state (a band rather than a number — *stable*, *strained*, *deteriorating*, *crisis*, *improving*) and a directional indicator (improving / worsening / steady). The bands are derived from graph state — the activation of conditions in each dimension's subgraph, weighted by edge weights.

The five-dimension scheme is not the final word. Calibration may reveal that some dimensions should split or merge. But starting with five forces the system to commit to a digest of the world's state rather than dumping a graph at the user.

### Level 2 — Rate and acceleration

The same five dimensions, viewed as time series rather than current state. Answers *"how fast is each dimension moving?"*

For each dimension, a sparkline-style time series showing:
- The recent state band history (say, last 90 days).
- The rate of change (slope).
- The acceleration (rate of rate-of-change) flagged when it changes sign — a worsening that's now slowing matters; a stable trend that's now accelerating matters more.

Rate and acceleration are presented as qualitative annotations on the time series, not as numerical values. The principle from the schema document — ordinal not probabilistic — extends to the UI: the user sees *"deteriorating, but more slowly than last month"*, not *"-0.34 with slope +0.07."*

### Level 3 — What changed in the last 24 hours

The delta view. Answers *"what's happened since I last looked?"*

The decision rule is deliberately simple: **show the most impactful graph changes from the last 24 hours**, ranked by impact.

A graph change is impactful when:
- A high-weight edge strengthened or weakened.
- A condition activated or deactivated.
- A new edge was created with a starting weight of `moderate` or higher.
- A disconfirmation pass meaningfully weakened a previously high-weight edge.
- A new event node was created that connects to active conditions.

The 24-hour window is fixed, not configurable. The point is rhythm — Augur is something you can check daily and reliably see what mattered yesterday. Longer windows are available via the time scrubber (level 4), not by adjusting this view.

Each change shows in compact form:
- One sentence describing what changed.
- The dimension(s) it affects from level 1.
- A "drill in" affordance leading to the reasoning layer (level 5).

If 24 hours produced nothing impactful, the panel says so. The system does not manufacture interesting changes to fill space.

### Level 4 — In your context

The personal scope. Answers *"what does this mean for me?"*

Personalization is **geographic only**. The browser provides geolocation; Augur uses it to determine which regional subgraphs are most relevant to the user's physical location, and re-scopes the levels above through that filter.

For a user in Tønsberg, "in your context" might emphasize:
- Energy markets relevant to Norway and the Nordic region.
- Migration and food security signals affecting Europe.
- Arctic and North Atlantic geopolitical signals.
- Currency and capital flows affecting NOK and the Eurozone.

For a user in Lagos, the same graph would foreground:
- West African and Sahel signals.
- Commodity prices (oil, cocoa, palm) relevant to local economies.
- Migration and food security signals affecting Sub-Saharan Africa.
- Naira and capital flows in the relevant trade corridors.

The graph is the same. The scope is geographic. There are no user preference settings, no opt-in topics, no saved searches. The user sees Augur scoped to where they are, and that's all.

#### Design decision: geolocation-only personalization

The alternative considered was rich personalization — letting users encode their interests, save topics, mark preferences, and customize the view.

The case for the current design (geolocation only):

- Personal preference settings color the result too much. A user who marks "I'm interested in technology" will see a Tech-flavored world that other users won't, and the comparability of what Augur shows different users degrades.
- The transparency principle from the vision is better served by everyone seeing the same graph, scoped only by physical context.
- It removes a whole class of bias-amplification: there's no way for a user to inadvertently filter themselves into a comfortable subset of the graph.
- It removes a maintenance burden: no settings UI, no preference storage per user, no migration when preference schemas change.

The case for the alternative (rich personalization):

- Users have legitimately different interests within their region. An energy trader in Tønsberg cares about different things than a teacher in Tønsberg.
- The "what does this mean for me" question is genuinely answered better with more user context.
- Most successful information products personalize heavily, and there are reasons for that.

**Current decision: geolocation only.** The bias-amplification risk is the deciding factor. Augur is built to expose how the world looks, not to give each user a personalized version of how the world looks.

**Revisit trigger:** if operator review reveals that the geographic scoping is too coarse to be useful — e.g., users consistently complaining that the regional view is irrelevant to their actual professional concerns — consider adding *occupation* or *interest domain* as a second scoping axis. Even then, the scoping should be ordinal (small number of broad categories) rather than free-form preference encoding.

### Level 5 — The reasoning

The graph-traversal view. Answers *"why does Augur think this?"*

This is the deepest level and the only one where the graph structure is exposed directly. It is reached by drilling in from any of the levels above — clicking a dimension, a change, a condition, or a node leads here.

The reasoning view for any node or edge shows:

- **The claim** — what the node represents or what the edge asserts.
- **Current weight and state** — band, not number. Activation status for conditions.
- **The reasoning text** — the structured explanation written by anchoring at the time the node or edge was created or last meaningfully updated.
- **Supporting signals** — list of signal records that established or maintain this. Each signal cites its source.
- **Disconfirming signals** — list of signals that have pushed against this, if any.
- **The falsification criteria** — what evidence would weaken this edge.
- **Weight history** — a time series showing how the weight has changed over time, with annotations on what caused each change.
- **Connected nodes and edges** — a small focused subgraph showing the immediate neighborhood, navigable by clicking outward.
- **The Langfuse trace** — for operators or trusted collaborators, a link to the LLM call that produced the anchoring. Hidden from regular users.

The reasoning view is dense. It is meant to be read carefully, not scanned. It is also the view that satisfies the vision document's transparency principle most directly — every claim is one or two clicks from its supporting evidence.

---

## The home view

The primary surface of Augur. A single page that the user lands on, designed to answer levels 1, 2, 3, and 4 above with minimal navigation.

Layout description (specified as content and ordering, not pixel placement):

**Top region — The trajectory** *(answers level 1)*

The five dimensions with their current state bands and direction indicators. Compact. One row of five items. The user sees this in the first half-second.

**Second region — The rate** *(answers level 2)*

Sparkline-style time series for the same five dimensions, with rate and acceleration annotations. Same horizontal layout as the top region, one level down. The user sees this without scrolling.

**Third region — What changed today** *(answers level 3)*

A ranked list of the most impactful changes from the last 24 hours. Each change is a compact card: one sentence, dimension tag, drill-in affordance. Probably 5-10 items.

**Fourth region — In your context** *(answers level 4)*

The same trajectory and changes, but scoped to the user's geographic context. Smaller than the global view above, because it's the same information through a different lens.

**Bottom region — Topics**

Entry points into the major causal domains for exploration. These are not the dimensions from level 1 (which are abstract); these are concrete topical areas of the graph: *"Iran-Israel and regional energy," "Fertilizer and food chains," "Central bank gold positioning," "Semiconductor supply,"* etc. Topics emerge from graph clustering and are curated by the operator. Clicking a topic leads to a topic view (described below).

**Persistent affordances on every screen:**

- The **time scrubber** (described in its own section below).
- The **conversation entry point** (described in its own section below).
- A small breadcrumb showing where in the zoom hierarchy you currently are.

---

## The topic view

When the user clicks a topic from the home view, they reach a topic view. This is the middle layer between home and the reasoning view — focused enough to be useful, broad enough to give context.

A topic view contains:

- A short prose summary of the topic's current state, generated from graph state.
- The active conditions in the topic's subgraph, listed by weight.
- The most impactful changes within the topic over the last 7 days (longer window than home's 24-hour view, because the user has chosen to focus).
- A focused subgraph diagram showing the topic's central nodes and edges. This is where graph visualization actually appears — small, focused, navigable, not the entire graph.
- A list of related topics, linked.

Topic views are reached by drilling in from home or from level 5 nodes. They are the natural "settle in and read about this" surface.

---

## The time scrubber

A persistent affordance across the entire interface. One of Augur's most distinctive features and one of its most powerful.

### What it does

The scrubber sets the `as_of` timestamp that powers replay mode (see `augur-architecture.md`). When the scrubber is at "now," the user sees current graph state. When the scrubber is moved backward, every view re-renders to show what Augur would have shown at that historical moment.

The user can see:
- What the world's trajectory looked like a week ago, a month ago, a year ago.
- Which edges existed then but don't now (or vice versa).
- Which conditions were active then but aren't now (or vice versa).
- What the home view's "what changed today" panel showed on a specific historical day.

### Why it matters

The scrubber is the affordance that distinguishes Augur from a news app. News tells you what is happening now. The scrubber lets you see *how Augur's understanding of the world has evolved.* You can scrub back to when a major event began and see what Augur knew (or didn't) at that moment. You can scrub forward through a calibration replay to watch the graph build up.

It's also a tool for honesty. If you suspect Augur is being too confident about a current claim, scrubbing back lets you see how recently that claim emerged, how much corroboration it has, and whether the system was previously uncertain.

### Interaction

The scrubber is a horizontal control with the current date at the right edge and configurable historical depth to the left (default: 12 months, with the ability to extend further). It is visible on every view, perhaps as a slim bar at the bottom of the screen.

Moving the scrubber updates all visible content. The transition is smooth where possible — graph changes are animated, not snapped. The user should be able to *see* the world's state evolving as they scrub.

### What the scrubber does not do

- It does not let the user write to the graph at a historical timestamp. Replay is read-only from the user's perspective.
- It does not predict forward. The right edge is always "now"; scrubbing forward past now does nothing. Projection is a separate affordance (described below).

---

## Projection — exploring possible futures

Projection is the level-5 question reframed in the forward direction: *"if X persists, what becomes more or less likely?"*

This is reached not from the home view but from a specific node or edge. When the user is looking at a condition (e.g., "Iranian crude exports through Hormuz constrained"), they can ask Augur to project forward from this state.

The projection view shows:

- A branching tree of conditional trajectories. Each branch is a path through the graph from current activated conditions outward.
- Each branch has an ordinal weight (drawn from the band scheme — `strong`, `moderate`, `weak`, `provisional`) indicating how supported the trajectory is.
- For each branch, the most fragile link — the edge whose weakening would most disrupt the trajectory.
- For each branch, the falsification signals — what would have to be observed to invalidate this path.

The principle is preserved from the vision: projections are branching and conditional, never single-future. The UI enforces this by structurally refusing to collapse multiple branches into one "Augur's prediction."

Saved projections become `Scenario` nodes (see `augur-graph-schema.md`), reachable from the user's history and revisitable as the graph evolves.

---

## The conversation layer

A natural-language interface, wrapping the visual surface rather than replacing it.

### What it does

At any point, the user can ask Augur a question in natural language. The conversation is grounded in the current view — what's on screen, what graph state is being shown, what the time scrubber is set to. The answer is produced by an LLM call that has the relevant subgraph as context.

Example questions the conversation handles well:

- "Why does Augur think Hormuz disruption is contributing to fertilizer constraints?"
- "What would have to happen for the geopolitical tension dimension to improve?"
- "Show me which edges connecting Iran and the global food market have weakened in the last month."
- "Compare what the energy view looked like in March 2024 to now."
- "What's the strongest disconfirming evidence for the active condition 'Russian gold reserves accumulating'?"

### How it grounds itself

The conversation layer does not hallucinate. It is constrained to reasoning from the graph state at the current `as_of` timestamp. Every claim in its responses links back to specific nodes, edges, or signals. The conversation cannot tell the user something the graph does not support.

When the user asks a question the graph cannot answer, the conversation says so explicitly: *"I don't have evidence in the graph relevant to that question."* This is preferable to fabricating an answer.

### Cost discipline

Conversation is the most LLM-expensive surface of Augur. The cost is controlled by:

- **A dedicated OpenRouter key with free-tier-only access** for the user-facing conversation. Conversation runs on free or near-free models; quality is acceptable because the graph context is doing most of the reasoning work.
- **The conversation can be turned off entirely without breaking the system.** Every other surface in the interface works without it. The conversation is an enhancement, not a foundation.
- **Conversation history is per-session, not persistent.** Augur does not maintain a memory of past conversations. Each session starts fresh, grounded in the current graph.

### Exploration without conversation

A first-class requirement: **the full interface must be navigable without ever using the conversation layer.** Click-and-drill paths must reach every piece of information the conversation can surface. Conversation is a faster path for users who want it; it is not the only path.

#### Design decision: conversation as enhancement, not foundation

The alternative considered was making conversation the primary interaction model, with visual views as supporting context.

The case for the current design (conversation as enhancement):

- Visual exploration is robust to LLM failures, free of inference cost, and accessible to users who prefer not to type.
- The graph itself is the authoritative artifact. The conversation is a way to navigate it; making it primary would put a fragile layer in front of the durable layer.
- It matches the vision document's user-as-peer principle. A peer wants to see the artifact, not be told about it.

The case for the alternative (conversation as primary):

- Natural language is the most powerful interface for exploring complex causal structures.
- Many users prefer asking questions to clicking.
- Modern AI products have demonstrated that conversation can be the primary surface successfully.

**Current decision: conversation as enhancement.** The cost and robustness arguments win for a single-operator system with limited LLM budget. The decision is also reversible if free-tier model quality improves enough that conversation becomes effectively free.

**Revisit trigger:** if free-tier models reach a quality where graph-grounded conversation is consistently better than click-and-drill exploration, consider promoting conversation to primary and making visual views secondary.

---

## Aesthetic principles

Augur's aesthetic is **research tool, not news app.**

- **Calm over urgent.** No red banners, no breaking-news framing, no notifications. Augur is for reflection.
- **Dense over spacious.** Information density is favored. The user is a peer, not a tourist; they can handle a screen with a lot on it if the content is meaningful.
- **Sober over decorative.** Visual flourishes are kept to a minimum. The graph is the substance; chrome should disappear.
- **Static over animated.** Animations are limited to functional transitions (time scrubber changes, drill-down reveals). No decorative motion.
- **Reading over scanning.** Layouts assume the user is reading carefully, not glancing. Long-form text is allowed where it serves comprehension.
- **Typography over imagery.** Text is the primary mode. No stock photography, no AI-generated illustrations, no decorative iconography.

Reference points: the inner pages of the Economist or the FT for typography and density. The Bloomberg Terminal for information layout. Wikipedia for the click-to-drill-in pattern. Edward Tufte's information design for the time series and small-multiples treatment.

Anti-reference points: most modern news apps (CNN, NYT mobile, Apple News). Most dashboards aimed at executives. Anything that uses motion graphics to make data feel exciting.

---

## What presentation deliberately does not include

- **No notifications.** Augur does not push updates to the user. The user comes to Augur, not the other way around.
- **No engagement metrics.** No streaks, no badges, no "you've checked Augur 47 days in a row." The system does not try to retain attention.
- **No social features.** No comments, no sharing, no other users visible. Even if Augur eventually has multiple trusted users, they do not see each other in the interface.
- **No recommendation algorithms.** The home view shows what graph state says is impactful, full stop. There is no per-user signal about what they previously clicked into.
- **No saved searches or alerts.** If the user wants to track a topic, they click into its topic view. The system does not maintain monitors on the user's behalf.
- **No undo on operator actions in the user surface.** User-side annotations (level 5 challenges) are deliberate; the user is treated as someone making considered claims.
- **No mobile-first design.** The interface is designed for a laptop or desktop browser. Mobile is allowed to be cramped.
- **No light-mode-only or dark-mode-only mandate.** Both should exist, defaulting to system preference.

---

## The progression from minimal to full

The full interface described above is the target, not the first build. A minimal first version covers:

1. The home view's top three regions (level 1, level 2, level 3).
2. The reasoning view (level 5), reachable from drill-down.
3. The time scrubber, even if initially limited in range.

Subsequent additions, in rough order:

4. The topic view.
5. The geographic scoping for level 4.
6. Projection.
7. The conversation layer.

Each addition is independently testable. The minimal version is enough to start using Augur for the questions it's designed to answer; the additions deepen the experience but are not preconditions for the system being useful.

The conversation layer is deliberately last in the build order. The visual interface should be robust on its own before LLM-driven conversation is added, so the conversation is enhancing a working system rather than papering over an incomplete one.

---

## Closing principle

The user comes to Augur with the questions. The interface's job is to answer them in the right sequence, at the right zoom level, with the reasoning always one or two clicks away. Anything else is decoration.
