# Augur — Vision

*Foundational document. Everything else in the project inherits from this.*

---

## What Augur is

Augur is a **reasoning prosthetic for understanding the present and exploring plausible futures**.

It ingests structured signals from the world — news, central bank flows, shipping, seismic activity, aircraft movements, mining production, commodity prices, and similar physical and economic data — and maintains a navigable causal graph that represents how those signals connect.

A user can:

1. Look at the current state of the graph and see what is happening, with full source provenance.
2. Walk the graph forward to explore conditional futures: *"if X persists, then Y becomes more likely, but Z would shift the trajectory."*
3. Inspect, challenge, or fork any node or edge in the graph.

Augur is not a forecasting engine that delivers predictions. It is a **shared reasoning artifact** that makes the kind of multi-thread causal thinking some people do informally available as something the user can navigate, audit, and extend.

> **Other framings considered.** "Reasoning prosthetic" is the conservative description and the one this document commits to. Other framings explored during design include *epistemic infrastructure*, *shared causal model of the world*, *navigable thinking tool*, and *open-source intelligence synthesis system*. Each implies a slightly different scope. The current framing is deliberately the narrowest of these, because narrow framings are easier to build toward and easier to honestly evaluate. As Augur matures and the boundaries of what it actually does become clearer, the framing may broaden.

---

## What Augur is not

Stating this explicitly because it shapes every downstream decision:

- **Not a business.** Augur exists to support transparency and agency, not to generate revenue. This frees it from optimizing for engagement, retention, or virality, all of which corrupt epistemic quality.
- **Not a prediction engine.** Augur does not say "X will happen." It says "given current signals, these trajectories are more or less likely, and here is the reasoning."
- **Not a news aggregator.** News scraping is an input, not the product. The graph is the product.
- **Not a single-future projection tool.** Outputs are conditional and branching, never a committed forecast.
- **Not an oracle.** The LLM's role is to extract and structure signal, not to pronounce on the future.
- **Not a replacement for expertise.** A domain expert reading Augur should find it useful as scaffolding; they should not feel it is telling them something they didn't already understand.

---

## The problem Augur addresses

Most people who read the news either ignore it or worry about it. Few people systematically connect what they read into a coherent picture of cascading consequences, even though the connections are often not hidden — they are simply tedious to maintain in one's head.

A concrete example: the disruption of Iranian and regional fertilizer supply chains in 2026 was reported widely. The downstream consequences — reduced crop yields in import-dependent regions later that year, food price increases the following year, disproportionate impact on poorer countries, increased migration pressure, additional strain on trade relationships — were also reported, but in separate articles, on separate days, in separate sections, by separate journalists. The causal chain is **knowable but not legible**.

The bottleneck is not access to information. The bottleneck is the cognitive cost of holding twelve causal threads in your head at once and noticing where they intersect.

Augur exists to externalize that cognitive work.

---

## Core design principles

### 1. Legibility over prediction

Augur's primary output is a graph that can be read and traversed, not a forecast. Success is measured by whether the graph helps a user think more clearly about the world, not by whether any specific prediction comes true.

### 2. Transparency over confidence

Every edge in the graph carries its supporting evidence, its source provenance, and an explicit statement of what would weaken or invalidate it. The user should always be able to ask "why does Augur think this?" and get a real answer.

### 3. Branching over committed forecast

Future projections are walks on the graph that produce conditional probabilities across multiple trajectories, never a single predicted future. The output shape is *"if A, then B is more likely; if A and C, then D becomes plausible; the most fragile assumption is E."*

### 4. Physical reality over narrative

When physical-world data (shipping, seismic, central bank flows, satellite imagery, trade records) and narrative reporting disagree, physical data wins. Narrative is treated as evidence of what people are being told, which is itself useful but distinct from what is happening.

### 5. Provenance and disconfirmation as first-class

Every signal carries its source and tier. Every high-weight edge is periodically challenged: "what evidence in the last period would weaken this?" Confirmation bias is the default failure mode of any system like this, and only an explicit disconfirmation discipline prevents it.

### 6. Slow signal over fast signal

Augur is biased toward signals with strategic significance and long half-lives (central bank gold purchases, treaty changes, infrastructure decisions, multi-month flow shifts) over reactive narrative (daily news cycles, social media sentiment). Fast signals are ingested but heavily discounted unless they corroborate slow signals.

### 7. The user is a peer, not a consumer

Augur is designed for people who want to think with it, not be told things by it. The interface, the output, and the underlying model all assume an engaged user who will inspect reasoning, challenge edges, and contribute priors.

---

## Who Augur is for

A small, self-selecting audience:

- People who are systems thinkers by temperament and want scaffolding for the reasoning they already do informally.
- Researchers, analysts, journalists, and serious hobbyists who care about epistemic honesty more than confident takes.
- Domain specialists who want a cross-domain view that respects their expertise within their domain.
- People who are skeptical of pundits and want a tool that exposes its reasoning rather than packaging conclusions.

Augur does not try to serve the general news-consuming public. The general news-consuming public is well-served by existing media, and the design tradeoffs required to serve them (simplification, confident framing, engagement optimization) are incompatible with Augur's purpose.

---

## A note on authorship and intent

Augur is built by one person, primarily for that person's own use, with the design assumption that the audience is people who think similarly. It is not a product of an institution and does not aspire to be one. The choice of sources, the structure of the graph, the selection of which signals to prioritize, and the framing of what counts as a useful output all reflect the priors of its author.

This is acknowledged rather than hidden. A vision document for a project of this kind can either pretend to a neutrality it does not have, or it can name its grounding honestly. The latter is more useful — to the author, to anyone who collaborates later, and to anyone who uses Augur and wants to understand what is in it and why.

The practical consequence is that design decisions throughout the project should be evaluated against the question *"does this serve the kind of reasoning Augur is built to support?"* rather than *"would this be valuable to a hypothetical general user?"* If Augur ever opens to broader collaboration, this document is one of the first that will need to be revised.

---

## What success looks like

Augur is succeeding when:

1. A user can point at a current condition in the graph and trace its evidentiary basis back to primary sources within two or three clicks.
2. A user can pose a "what if" question and get a branching answer with conditional probabilities and reasoning, rather than a single forecast.
3. The graph identifies meaningful causal connections at least a few weeks before they become visible in mainstream coverage, because the underlying signals (shipping flows, central bank behavior, seismic activity, etc.) are leading indicators.
4. Users disagree with specific edges and modify them, and the system supports that disagreement gracefully — meaning the graph is genuinely a shared artifact rather than a fixed product.
5. Users describe Augur as "useful to think with" rather than as "accurate."

Augur is failing when:

1. Users treat it as an authority on the future.
2. The graph accumulates confirmation bias and the disconfirmation pass is not effective.
3. The signal layer is noisy enough that the graph becomes unreliable, undermining trust.
4. The output looks confident in domains where Augur should be uncertain.
5. The system optimizes for being interesting rather than being honest.

---

## Scope boundaries

A few things Augur cannot do and should not pretend to do:

- Augur cannot predict specific events. It can describe the conditions under which categories of events become more or less likely.
- Augur cannot quantify probability with precision. Edge weights are ordinal and qualitative, not calibrated probabilities. Anyone treating an edge weight as a precise probability is misusing the system.
- Augur cannot replace domain expertise. It can connect insights across domains, but the depth in any single domain is shallower than what a specialist provides.
- Augur cannot see what it does not ingest. Closed regimes, opaque actors, and undisclosed activities are gaps in the graph that the system should explicitly acknowledge rather than paper over.
- Augur cannot eliminate its own biases. The choice of sources, the design of lenses, and the structure of the graph all reflect the priors of whoever built them. Transparency about those priors is the only honest response.

---

## Relationship to existing thinking

Augur draws on but is distinct from several adjacent traditions:

- **From systematic forecasting and superforecasting:** the discipline of breaking complex questions into smaller, more tractable sub-questions and reasoning about conditional probabilities. But Augur is not trying to win forecasting tournaments; it is trying to support reasoning.
- **From scenario planning:** the practice of building multiple plausible futures rather than committing to one. But Augur generates scenarios continuously from live signal rather than at periodic planning intervals.
- **From open-source intelligence (OSINT):** the practice of synthesizing public physical-world data into actionable understanding. Augur is OSINT-flavored in its source mix, but its output is reasoning structure, not investigative finding.
- **From causal inference and Bayesian networks:** the formal apparatus of graphs with conditional dependencies. Augur uses this shape informally; it does not claim mathematical rigor in its weight updates.

What Augur does that none of these does individually: maintain a **continuously updated, navigable, multi-domain causal graph** as a living artifact that a user can think with.

---

## Why this project, why now

Two things have changed recently that make this kind of project tractable in a way it wasn't five years ago:

1. **LLMs can do structured extraction at scale.** Reading thousands of articles a week and converting them into structured signal claims with provenance is now a tractable engineering problem, not a research problem.
2. **Physical-world data is more accessible than ever.** ADS-B, AIS, USGS feeds, IMF reserve data, satellite imagery, and trade flows are all available via APIs that did not exist or were inaccessible to individuals a decade ago.

The combination — cheap structured extraction plus rich physical signal — is what makes Augur feasible as a personal-scale project rather than something requiring an institutional team.

---

## Closing frame

Augur is not trying to be right about the future. It is trying to make the present and its plausible futures more **legible** — to the person building it, and to anyone else who wants to think more clearly about where the world is going and why.

The measure is not accuracy. The measure is whether the graph is useful to think with.
