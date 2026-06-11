# Augur — Sources

*The registry of where Augur's signal comes from. Tier definitions, perspective pools, source classification rules, and the initial source list. Inherits from `augur-vision.md`, `augur-architecture.md`, and `augur-signal-pipeline.md`. Source confidence scoring methodology is detailed in `augur-calibration.md`.*

---

## Reading guide

This document is the source-of-truth registry. When the ingestion layer needs to know which sources to fetch from, what tier they sit in, what perspective they represent, and what their starting confidence weight is, those answers live here.

Cross-references:
- Why source curation matters → `augur-vision.md` (principles 4 and 5)
- How ingestion consumes sources → `augur-architecture.md` and `augur-signal-pipeline.md` (stage 1)
- Source confidence scoring methodology → `augur-calibration.md`
- Search infrastructure (SearXNG) → this document, "Search infrastructure" section

---

## The principle behind source curation

Source curation is the single highest-leverage activity in the entire Augur pipeline.

A poorly curated source list produces noise faster than the extraction layer can process it, drowns the graph in low-quality signal, and inflates apparent consensus through downstream echo. A well-curated source list lets the rest of the pipeline operate with much higher signal-to-noise, which compounds at every downstream stage.

The default assumption — that broader is better — is wrong. Most articles on most topics are reprints, rewrites, or downstream echoes of a small number of primary sources. Ingesting 5,000 articles a day where 4,900 are reprints of 100 primary sources is worse than ingesting just the 100 primary sources, because the 4,900 reprints actively corrupt the convergence detection: ten outlets reprinting Reuters does not mean ten independent sources agree.

Augur prefers fewer high-quality sources to more low-quality sources. This is enforced by the tiering system below.

---

## Source tiers

Five tiers. Every source is classified into exactly one tier.

### Tier 0 — Primary data and official statements

Direct, authoritative, machine-readable where possible. The signal here is the closest available approximation of "what actually happened" rather than "what someone reported."

**Characteristics:**
- Government, multilateral institution, or producer-direct.
- Usually structured (API, CSV, formal release).
- High latency tolerance from publication to ingestion is acceptable (these sources are slow but trustworthy).
- Almost never wrong about what they're directly reporting, though their scope is narrow.

**Starting source weight: 0.9–1.0.**

### Tier 1 — Specialized analysts and trade press

Domain specialists who consume Tier 0 directly and add interpretation, context, and synthesis. The signal here is "what does this primary data mean."

**Characteristics:**
- Sector specialists with deep domain expertise.
- Often paywalled or behind subscription.
- Mix of factual reporting and analyst opinion (must be tagged accordingly when extracted).
- Generally accurate within their domain; opinions are clearly attributed.

**Starting source weight: 0.7–0.85.**

### Tier 2 — Major wires and general financial press

The international news layer. Reuters, AP, Bloomberg, AFP, Xinhua, TASS, PTI, Anadolu, FT, WSJ, NYT, Economist. The signal here is "what is being reported widely as fact."

**Characteristics:**
- High volume, broad coverage.
- Multiple perspectives represented across the wire ecosystem.
- Standards-driven editorial (most of the time) but not immune to error.
- The first place most cross-domain stories appear in legible form.

**Starting source weight: 0.5–0.7.**

### Tier 3 — National general-interest press and credible regional outlets

Domestic newspapers, regional outlets, public broadcasters. The signal here is "what is being told to a particular national audience."

**Characteristics:**
- Often the best source for what a population is being told about an event.
- Useful primarily for `narrative_divergence` lens; less useful as factual evidence.
- Quality varies enormously within this tier.

**Starting source weight: 0.3–0.5.**

### Tier 4 — Aggregators, opinion, social, and crowdsourced

Aggregators, opinion sites, blogs, social media accounts, niche newsletters, crowdsourced data platforms. The signal here is "what is being discussed, by whom, with what framing."

**Characteristics:**
- Almost never useful as factual evidence.
- Sometimes useful as early-warning signal for emerging stories before mainstream coverage.
- Often useful for narrative and sentiment signals.
- Easily manipulated; must be heavily discounted.

**Starting source weight: 0.1–0.3.**

### Special case: structured-data sources

Sources like ADS-B Exchange, AIS providers, USGS earthquake feeds, FRED economic data, IMF reserve data — these are technically Tier 0 in trustworthiness but they don't fit the narrative-source model. They are handled separately in the `physical_world` lens (see `augur-signal-pipeline.md`).

Their "source weight" works differently: rather than weighting their narrative claims, the system weights the operational reliability of the API (uptime, latency, completeness) and treats the underlying data as `hard_datum` confidence by default.

---

## Perspective pools

Tiering captures source quality. Perspective pools capture **source standpoint** — whose worldview the source reflects.

Augur maintains multiple perspective pools and treats signals from different pools as distinct evidence even when they make similar claims. The convergence of independent perspectives is one of the strongest signal types in the system; merging perspectives into a single pool destroys this.

### Initial perspective pools

1. **US/EU** — North American and Western European sources. The dominant perspective in English-language news. Includes Reuters, AP, Bloomberg, FT, NYT, Economist, major broadcasters, EU institutional press.

2. **India** — Indian English-language and major Indian-language press. Particularly important for South Asia, agriculture, energy, and IOR maritime affairs. Includes PTI, The Hindu, Indian Express, Economic Times, Mint, Hindustan Times, Times of India.

3. **China** — PRC state and state-aligned media. The PRC perspective on the world. Includes Xinhua, People's Daily, Global Times, CGTN, South China Morning Post (Hong Kong, semi-aligned), Caixin (more independent), and major Mandarin-language outlets when translation infrastructure permits.

4. **Russia** — Russian state and state-aligned media. The Russian perspective. Includes TASS, RIA Novosti, RT (English), Kommersant, Vedomosti (when accessible), Interfax.

5. **Gulf and Levant** — Arabic-speaking and Gulf-aligned press. Particularly important for energy and MENA affairs. Includes Al Jazeera, Asharq Al-Awsat, Al-Arabiya, The National (UAE), Arab News, Lebanon's L'Orient-Le Jour.

6. **Nordic** — Nordic-language and Nordic-perspective sources, given the project's operator location and interest in Nordic-relevant signal. Includes NRK, DR, SVT, YLE, Aftenposten, DN, Helsingin Sanomat, Reuters Nordic desk, plus specialized energy and Arctic-affairs sources (NVE, Statnett, Norwegian Petroleum Directorate).

7. **Latin America** — Latin American Spanish- and Portuguese-language press. Important for commodity production (agriculture, copper, lithium), South American politics, and US-aligned-but-distinct perspective on Western hemisphere affairs. Includes O Globo, Folha de São Paulo, El País (LatAm editions), Clarín, La Nación, El Comercio.

8. **Africa** — African press across multiple languages. Important for migration, mineral production (cobalt, gold, rare earths), conflict signals, and food security in import-dependent regions. Includes Mail & Guardian, The Continent, Daily Maverick, The East African, Premium Times (Nigeria), Daily Nation (Kenya), Jeune Afrique.

9. **Southeast Asia and Pacific** — sources covering ASEAN, Oceania, and the broader Indo-Pacific from non-aligned standpoints. Important for shipping (Malacca, South China Sea), semiconductors, and Pacific security. Includes Nikkei Asia, The Straits Times, Tempo (Indonesia), The Jakarta Post, Bangkok Post, ABC News (Australia), The Sydney Morning Herald.

### Why the split

Earlier drafts collapsed pools 7-9 into a single "Global South (non-aligned)" pool. The split exists because divergence within that pool is real and consequential.

A China-Taiwan crisis would be reported very differently by Southeast Asian sources (directly affected by supply chains, shipping disruption, and security posture) than by Latin American sources (more distant, commodity-price effects dominant) than by African sources (semiconductor supply effects via consumer goods, but otherwise marginal). Treating them as one pool would lose this divergence and weaken the convergence detection that depends on independent perspectives saying the same thing.

The split also lets Africa-specific signals (mineral production from DRC, food import stress in Sahel, migration pressure indicators) cluster with their natural regional context rather than getting averaged into a global aggregate.

### How perspectives are used

- Every payload is tagged with its perspective at ingestion time, based on the source registry.
- The `narrative_divergence` lens explicitly looks for the same event being framed differently across perspectives.
- Tier A's cross-perspective convergence detection rewards claims that appear across multiple perspectives more than claims that appear repeatedly within one perspective.
- The operator UI lets you filter graph evidence by perspective, so you can ask *"is this edge supported by signals from multiple perspectives, or only one?"*

### What is and isn't a perspective

A perspective is **not** a political alignment. Russian state media is a perspective even though it's politically aligned with the Russian state, because what it tells the Russian-reading audience is information about what that audience is being told. Treating it as evidence-of-fact requires care; treating it as evidence-of-narrative is straightforward.

Augur does not adjudicate which perspectives are "right." It records what each perspective says, and surfaces the divergences.

### Design decision: Nordic pool prominence and operator location bias

The Nordic perspective pool is structurally over-represented in Augur's initial design relative to its share of global signal production. Nordic sources occupy their own pool alongside major-power perspectives (US/EU, China, Russia, India), and Nordic-specific Tier 0 sources (NVE, Statnett, NPD) sit in the structured-data registry. A neutral global-signal system would not be built this way.

The current design preserves this prominence intentionally:

- The operator is in Norway. Nordic signals are directly relevant to the kind of reasoning Augur is built to support — energy markets, Arctic affairs, Nordic political and economic developments, regional implications of broader European trends.
- The vision document is explicit that Augur is built by one person for that person's use first. Reflecting the operator's geographic context in source curation is consistent with that grounding.
- Nordic structured data sources are unusually high-quality, transparent, and machine-readable (NBIM's security-level holdings, Statnett's real-time grid data, NVE's hydrology). Excluding them would forfeit signal that's genuinely better than the equivalent in most other regions.

The bias is real and worth naming explicitly:

- Augur will weight Nordic-perspective signals more heavily in convergence detection than a globally-neutral system would.
- Cross-perspective convergence calculations will count Nordic agreement on a claim as a full perspective, when arguably it should be weighted by the region's actual share of the affected world.
- The graph will likely accumulate Nordic-relevant nodes and edges at a higher rate than Latin American or African ones.

#### Possible future evolution: perspective as output scope rather than input pool

A more architecturally honest long-term approach would be to **decouple operator interest from perspective representation**. The reasoning:

- Perspective pools exist primarily to enable cross-perspective convergence detection and `narrative_divergence` lens work. For these purposes, perspectives should reflect *actual independent worldviews in the global media ecosystem*, not the operator's interests.
- Operator interest is better expressed as a **scoping or filtering concern on the output side** — "show me how this Iran-fertilizer chain affects Nordic conditions specifically" — rather than by inflating Nordic representation on the input side.

Under this future model:

- Nordic sources would still be ingested at appropriate volume.
- Nordic perspective would still be a pool (because Nordic media really is a distinct worldview within the broader European context).
- But Nordic-relevant graph nodes and conditions would be derived from the operator's *scoping queries* rather than from over-weighted Nordic signal.
- The same approach could be offered to operators in other regions: a Brazilian operator would get Latin-America-scoped outputs without needing Latin America to be over-represented on the input side.

**Current decision: keep Nordic prominent on the input side for now.** The architectural cost of separating input-pool weighting from output-scope filtering is non-trivial, and the bias is acceptable for a system explicitly built by and for one operator. Calibration will reveal how much the bias actually distorts downstream reasoning, and that will inform when the separation becomes worth building.

**Revisit trigger:** if calibration runs show that Nordic-weighted convergence detection is producing false-positive convergence (Nordic agreement counted as broad consensus when the rest of the world is silent), or if Augur is opened to operators in other regions, build the input-pool / output-scope separation. The migration would not require schema changes — it's a query-layer concern.

---

## Source classification rules

Every source in the registry has the following fields:

```yaml
source_id: <slug>
canonical_name: <human-readable name>
url_base: <homepage or API root>
tier: <0 | 1 | 2 | 3 | 4 | structured_data>
perspective: <pool ID>
languages: <list of ISO codes>
access_method: <playwright | http | rss | api | searxng | manual>
access_config: <method-specific config — endpoints, auth refs, rate limits>
update_cadence: <real_time | hourly | daily | weekly | monthly | quarterly | annual | event_driven>
domains: <list of topical domains this source covers well>
starting_source_weight: <0.0–1.0>
notes: <free text>
added_at: <date>
last_audited: <date>
```

**Tier and weight are decoupled from each other.** Tier is a structural categorization; weight is a numeric multiplier used during convergence scoring. A source's weight starts within its tier's range and is adjusted by calibration.

**`domains` enables lens-source affinity.** When the commodities lens is extracting from a payload whose source has `commodities` in its domains, that's a higher-confidence extraction than when an off-domain lens reads an off-domain source. The lens-source affinity matters less than the signal itself, but it's a useful tiebreaker.

**Source weights are not fixed.** Calibration (see `augur-calibration.md`) is where source weights are tuned based on the survival rate of the signals each source produces. Starting weights are best-guesses; calibration replaces them with empirical estimates.

---

## Search infrastructure

SearXNG is the search backbone for general web ingestion. It runs on the same VPS as Augur (see `augur-architecture.md`, "Existing infrastructure on the VPS").

### How SearXNG fits

SearXNG is **not a source** in its own right. It is a query mechanism for discovering payloads from configured sources.

The ingestion layer uses SearXNG in two patterns:

1. **Domain-scoped searches.** When the system needs recent coverage on a topic, it issues a SearXNG query restricted to specific source domains drawn from the source registry. This is the pattern for routine topic monitoring.

2. **Open searches.** Periodically, the system issues open searches on tracked topics without domain restrictions, looking for new sources that should be evaluated and potentially added to the registry. These don't directly produce signals; they produce *candidate sources* that the operator reviews.

### Configured search engines within SearXNG

SearXNG aggregates multiple upstream search engines. For Augur the recommended configuration:

- **General web:** Google, Bing, DuckDuckGo, Brave Search (rotating to spread load).
- **News:** Google News, Yandex News (good for Russian-perspective discovery), Baidu News (Chinese-perspective discovery).
- **Specialized:** SemanticScholar and arXiv for research-oriented queries, especially when the system encounters technical claims.
- **Regional:** Region-specific engines where they materially improve coverage of perspective pools that English-default engines under-represent.

### Query construction

Augur constructs SearXNG queries through a small set of templates rather than free-form prompting:

- `topic_recent`: `{topic} news after:{date}` — restricted to news vertical, recent window.
- `topic_in_perspective`: `{topic} site:{domain1} OR site:{domain2}` — domain-scoped to a perspective pool.
- `cross_perspective`: same `{topic}` issued separately to each perspective pool, results compared.
- `entity_emergence`: `{entity_name}` issued periodically to track new mentions and find new sources.

Query patterns are configuration, not code. They evolve as the system runs.

### SearXNG limits and how they shape ingestion

- Free upstream engines rate-limit aggressively. Augur respects these via SearXNG's built-in rate limiting and stages queries across time.
- SearXNG result quality on niche topics is mediocre. For high-confidence domain coverage, direct ingestion from source registries (RSS, APIs) is always preferred. SearXNG is for breadth, not depth.
- The system never trusts SearXNG result rankings as signal in themselves. Rankings are a starting point for fetching; the content is what matters.

---

## The initial source registry

This section catalogs the starting source set. It is **not exhaustive** — additions are expected throughout the project's life — but it is the concrete starting point.

Each entry below uses an abbreviated form. The full registry lives in a structured data file (`sources.yaml` or equivalent) that the system loads at runtime.

### Tier 0 — Primary data and official statements

#### Economic and financial

- **FRED** (Federal Reserve Bank of St. Louis) — fred.stlouisfed.org/docs/api — daily for most series — global central bank balance sheets, US economic indicators. *US/EU perspective, API.*
- **IMF Data** — data.imf.org — monthly — IFS for reserves, COFER for currency composition, SDDS for high-frequency country data. *Multilateral, API/download.*
- **World Gold Council Goldhub** — gold.org/goldhub — monthly — central bank gold movements per country. *Industry body, structured download.*
- **US Treasury TIC** — home.treasury.gov/data/treasury-international-capital-tic-system — monthly — foreign holdings of US Treasuries by country. *US/EU perspective, API.*
- **ECB Statistical Data Warehouse** — data.ecb.europa.eu — varies — Eurosystem balance sheet, APP/PEPP holdings. *US/EU perspective, API.*
- **NY Fed SOMA** — markets.newyorkfed.org — daily — Fed securities holdings at CUSIP level. *US/EU perspective, API.*
- **NBIM** — nbim.no — quarterly — Norway sovereign wealth fund holdings to security level. *Nordic perspective, structured.*
- **BIS Statistics** — bis.org/statistics — quarterly — international banking, FX, derivatives data. *Multilateral, structured.*

#### Physical and environmental

- **USGS Earthquake Hazards Program** — earthquake.usgs.gov/fdsnws/event/1 — real-time — global seismic events. *Physical data, API/websocket.*
- **EMSC** — seismicportal.eu — real-time — European-Mediterranean seismic events. *Physical data, websocket.*
- **USGS Mineral Commodity Summaries** — usgs.gov/centers/national-minerals-information-center — annual — world mineral production and reserves. *Physical data, structured download.*
- **USGS Mineral Resources Online Spatial Data (MRDS)** — mrdata.usgs.gov — slow — global mine locations and characteristics. *Physical data, structured.*
- **FAO** — fao.org — varies — agricultural and food security data. *Multilateral, API/download.*
- **EIA** — eia.gov — weekly to monthly — US energy data, global oil/gas estimates. *US/EU perspective, API.*
- **NVE** — nve.no — daily — Norwegian energy data, hydrology, electricity prices. *Nordic perspective, API.*
- **Statnett** — statnett.no — real-time — Norwegian grid operator data. *Nordic perspective, API.*
- **Norwegian Petroleum Directorate** — sodir.no — monthly — Norwegian oil and gas production. *Nordic perspective, structured.*

#### Aircraft and shipping

- **ADS-B Exchange** — adsbexchange.com — real-time — global aircraft positions including military and filtered aircraft. *Physical data, API.*
- **OpenSky Network** — opensky-network.org — real-time and historical — academic ADS-B archive. *Physical data, API.*
- **AISHub** — aishub.net — real-time — AIS vessel tracking, data-sharing cooperative. *Physical data, API.*
- **VesselFinder API** — api.vesselfinder.com — real-time — AIS data with paid tiers for better coverage. *Physical data, API.*
- **Global Fishing Watch** — globalfishingwatch.org — real-time — fishing fleet behavior, IUU detection. *Physical data, API.*

#### Trade and geopolitical primary

- **UN Comtrade** — comtradeplus.un.org — monthly to annual — international trade flows. *Multilateral, API.*
- **WTO Statistics** — stats.wto.org — annual — trade and tariff data. *Multilateral, API.*
- **Central bank press releases** (Fed, ECB, BoJ, BoE, PBoC, RBI, CBRT, others) — varies — direct statements from monetary authorities. *Multiple perspectives.*
- **National ministry releases** for foreign affairs, finance, defense, energy — varies — official statements. *Multiple perspectives.*

### Tier 1 — Specialized analysts and trade press

- **S&P Global Commodity Insights** — focused commodities and energy analysis. *US/EU perspective.*
- **Argus Media** — energy and commodities pricing and analysis. *US/EU perspective.*
- **MEED** — Middle East economic and project intelligence. *Gulf and Levant perspective.*
- **Caixin** — Chinese economic and policy reporting, semi-independent. *China perspective.*
- **Kpler / MarineTraffic research** — commodity flow analysis derived from AIS. *US/EU perspective.*
- **Jan Nieuwenhuijs / Voima Gold blog** — independent gold flow analysis, China-unreported gold tracking. *US/EU perspective.*
- **Nikkei Asia** — Asian economic and policy reporting from a Japanese standpoint. *Global South (non-aligned) perspective.*
- **Hellenic Shipping News** — shipping industry trade press. *US/EU perspective.*
- **Mining Journal / Mining.com** — global mining industry reporting. *US/EU perspective.*

### Tier 2 — Major wires and general financial press

- **Reuters** — global newswire. *US/EU perspective.*
- **AP** — global newswire. *US/EU perspective.*
- **Bloomberg** — financial wire and analysis. *US/EU perspective.*
- **AFP** — global newswire, francophone reach. *US/EU perspective.*
- **Xinhua** — PRC state newswire. *China perspective.*
- **TASS** — Russian state newswire. *Russia perspective.*
- **PTI** — Indian newswire. *India perspective.*
- **Anadolu** — Turkish newswire. *Gulf and Levant perspective.*
- **Financial Times** — UK-based financial press. *US/EU perspective.*
- **Wall Street Journal** — US-based financial press. *US/EU perspective.*
- **The Economist** — UK-based weekly analysis. *US/EU perspective.*
- **New York Times** — US general-interest international coverage. *US/EU perspective.*

### Tier 3 — National general-interest press and credible regional outlets

#### US/EU
- The Guardian, Le Monde, Der Spiegel, El País (Spain), Politico EU, BBC News.

#### India
- The Hindu, Indian Express, Hindustan Times, Mint, Economic Times.

#### China
- People's Daily, Global Times, CGTN, South China Morning Post.

#### Russia
- RT (English and Russian), Kommersant, Vedomosti, Interfax.

#### Gulf and Levant
- Al Jazeera (English and Arabic), Asharq Al-Awsat, Al-Arabiya, The National (UAE), Arab News, L'Orient-Le Jour.

#### Nordic
- NRK, DR, SVT, YLE, Aftenposten, Dagens Næringsliv, Helsingin Sanomat.

#### Latin America
- O Globo, Folha de São Paulo, El País (LatAm editions), Clarín (Argentina), La Nación (Argentina), El Comercio (Peru), El Tiempo (Colombia).

#### Africa
- Mail & Guardian, Daily Maverick, The Continent, The East African, Premium Times (Nigeria), Daily Nation (Kenya), Jeune Afrique.

#### Southeast Asia and Pacific
- Nikkei Asia, The Straits Times, Tempo (Indonesia), The Jakarta Post, Bangkok Post, ABC News (Australia), Sydney Morning Herald.

### Tier 4 — Aggregators, opinion, social, crowdsourced

This tier is **deliberately small at start**. Tier 4 sources are added cautiously because their noise-to-signal ratio is high and adding them blindly poisons the convergence detection.

Initial Tier 4 entries:

- **GDELT** — Global Database of Events, Language, and Tone. Useful primarily as a discovery layer for events that mainstream coverage has missed. Not direct signal.
- **Specific high-quality independent newsletters and Substacks** — added individually after operator review. The bar is high.
- **Selected open-source analyst accounts** — flight trackers, AIS analysts, OSINT collectives. Added individually with clear notes about their reliability domain.

Social media (Twitter/X, Mastodon, Reddit, Telegram) is **excluded from the initial source set**. The signal-to-noise ratio is too poor and the manipulation risk too high. The operator can manually flag specific accounts later if a strong case emerges.

#### Design decision: social media exclusion

The current design excludes all social media platforms from the source registry. The alternative considered was selective inclusion of specific high-quality accounts as Tier 1 or Tier 2 sources on narrow domains.

The case for the current design (full exclusion):

- Signal-to-noise ratio on raw platform feeds is catastrophically bad. Even narrowly filtered, most content is reaction, opinion, or rebroadcast rather than primary observation.
- Manipulation risk is unmatched by any other source type. Coordinated inauthentic behavior, bot amplification, deliberate disinformation, and engagement-optimized framing all distort signal at scale.
- Per-account verification of source quality requires constant operator attention. Account ownership changes, posting patterns drift, accounts get hacked or sold. The maintenance burden compounds.
- Augur's downstream pipeline assumes signals are independent observations. Social media signals are deeply non-independent — one viral post produces thousands of derivative posts that look like corroboration but are actually one signal echoed.

The case for the alternative (selective inclusion):

- Some accounts are genuinely Tier 1 quality on specific topics: military analysts who post original geolocation work, financial commentators with primary-source access, OSINT collectives doing real investigative work, regional specialists in conflict zones where institutional press has no access.
- Telegram channels in particular sometimes carry primary signal from regions where mainstream press is absent or restricted.
- Early-warning latency on social media is often hours to days ahead of mainstream coverage for breaking events.
- Excluding entirely means missing real signal in pursuit of avoiding noise.

**Current decision: full exclusion at the source-registry level.** The selective-inclusion approach is more architecturally complex than it looks: it requires per-account access patterns, per-account confidence scoring (separate from outlet-level scoring), per-account audit cadence, and platform-specific scraping or API integration. Building this responsibly is its own subsystem, not a small addition to the source registry.

**Revisit trigger:** if calibration runs and operational experience demonstrate that Augur is consistently missing real signal that social media surfaces ahead of mainstream coverage, build a dedicated `augur-social-ingestion.md` design and a separate ingestion subsystem with per-account confidence tracking. The integration cost is justified only after the gap is empirically demonstrated, not as a first-build decision.

### Structured-data sources (special category)

The Tier 0 physical and environmental sources listed above (USGS, ADS-B, AIS, FRED, etc.) are also categorized as structured-data sources. The `physical_world` lens consumes them directly without LLM extraction; values become signals via deterministic threshold and anomaly detection.

#### Market-data axis (2026-06)

Macro market series are a structured-data signal axis, sourced from **two independent free providers for corroboration** rather than one — FRED (already integrated) and **Yahoo Finance** (its public chart JSON endpoint, fetched directly over HTTP; no paid tier, no terminal, no heavyweight client library). Per the exclusions below, no paid market feed is used or depended on.

The curated set is deliberately small and macro — pulling *all* market data is noise:

- **Commodities:** gold, Brent + WTI crude.
- **Currencies:** the five USD majors (EUR, JPY, GBP, CHF, CAD/AUD) plus the broad dollar index.
- **Equities:** the ten largest exchange indices (e.g. S&P 500, Nasdaq, Euro Stoxx 50 / DAX, FTSE 100, Nikkei 225, Hang Seng, Shanghai Composite, Sensex) as a global risk-appetite read.
- **Rates:** the five most-watched 10-year sovereign yields (US, Germany, UK, Japan, and one more).

Per the structured-data rule above, these become signals **deterministically**: the fetcher computes the percentage change over a trailing window and emits a clean, pre-computed market-move statement (e.g. "Brent crude +4.2% over 5 trading days, $82.40 → $85.86") rather than handing a bare number to a prose lens to interpret. FRED and Yahoo are cross-checked on series both carry — agreement strengthens the signal, divergence flags a data issue (and feeds source calibration). Dimension mapping: commodities → resource / geopolitical; currencies, equities, and rates → economic stability.

Caveat: Yahoo's endpoint is unofficial (no SLA, may rate-limit). Acceptable for a single-operator public-data tool and consistent with the "must not depend on any one source" posture, since FRED corroborates the core macros.

#### Evaluated source additions (2026-06)

- **GDELT** — admissible (free, structured global event/tone data; not paid, social, or AI-generated). But it is a *news/events* axis, not market data, and a large firehose needing its own filtering and dedup against existing wire/RSS coverage. It goes through the normal source-admission process as its **own project**, separate from the market work.
- **Google Trends / `pytrends`** — **deferred, experimental.** Search-interest is *relative* (0–100 normalised), heavily rate-limited, and reached via an unofficial client; the "signal-to-noise doesn't justify the integration cost" concern that excludes social media applies here too. Revisit later as a possible *attention* signal, not a core source.

---

## Source admission process

Adding a new source is a deliberate operator action, not an automated discovery.

**The admission steps:**

1. **Candidate identification.** A source is proposed for inclusion — either by the operator, by SearXNG open searches surfacing a useful source repeatedly, or by being referenced by existing trusted sources.
2. **Tier assessment.** The operator categorizes the source into a tier based on the criteria above.
3. **Perspective assignment.** The source is tagged to its perspective pool.
4. **Initial weight.** Set within the tier's range, conservatively at the low end for new sources until calibration data exists.
5. **Domain tagging.** Topical domains the source covers well are recorded.
6. **Access configuration.** Method (API, RSS, scraping), endpoints, rate limits, auth credentials if needed.
7. **Audit cadence.** Set how often the source's weight should be re-evaluated against its signal performance.
8. **Test ingestion.** A few payloads are fetched and processed to validate the access pattern works end-to-end.

Sources can be retired similarly — marked deprecated, their existing signals preserved for historical traceability, but no new payloads fetched.

---

## Source confidence scoring

Source weights are not static. Calibration (see `augur-calibration.md`) is where they get tuned based on empirical evidence.

The high-level mechanic:

- During retroactive replay, each source's emitted signals are tracked.
- Signals that anchored to durable graph edges (still in the graph weeks or months later, possibly strengthened by corroboration) contribute positively to the source's weight.
- Signals that were contradicted by later evidence, or that anchored to edges later weakened by disconfirmation, contribute negatively.
- Signals that never anchored at all (extracted, but didn't make it past Tier A) are neutral.

The arithmetic is detailed in `augur-calibration.md`. The principle here is that the source registry is a **living document**, and the weights column is the most actively maintained field in it.

---

## What the source registry deliberately does not include

- **Closed paid sources** that require institutional subscriptions Augur doesn't have. Bloomberg Terminal, Eikon, FactSet, S&P Capital IQ, MSCI, Kpler, PitchBook — these are excluded at the architecture level, not just unaffordable. Augur is built to be operable from public-data sources. Paid sources can be added later as supplements, but the system must not depend on them.
- **Social media platforms.** Excluded from the initial set, as noted above. The signal-to-noise ratio doesn't justify the integration cost.
- **AI-generated content sources.** Sites that publish primarily LLM-written content are excluded outright. Their signal is downstream of other sources and inflates apparent consensus through synthetic echo.
- **Sources known to be deliberately disinformative.** Excluded even when they would be useful as `narrative_divergence` input. The line between "perspective" and "disinformation" is judgmental, but state-aligned media is treated as perspective; sites with documented histories of fabricating events outright are excluded.
- **Auto-discovered sources.** SearXNG open searches surface candidates for the operator to review; they don't auto-add. Letting the system add its own sources is how Augur would acquire confirmation bias drift over time.

---

## Revision posture

The source registry is the most living document in the project. It will be edited weekly during normal operation.

Stable parts:
- The five-tier system.
- The perspective pool definitions.
- The classification fields.
- The admission process.
- The exclusions.

Changing parts:
- The specific source list (additions and retirements continuous).
- Source weights (continuously tuned by calibration and audits).
- Tier boundaries — if a tier proves too broad or too narrow, the system may split or merge tiers (with care, because this affects every source in the affected tiers).

The registry's structured form (`sources.yaml` or equivalent) is the operational source of truth. This document describes the **shape and principles** of the registry; the file describes the **current contents**.
