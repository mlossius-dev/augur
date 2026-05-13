# Augur — Calibration

*Methodology for tuning source weights and validating signal extraction through retroactive replay. Inherits from `augur-vision.md`, `augur-architecture.md`, `augur-graph-schema.md`, `augur-signal-pipeline.md`, and `augur-sources.md`.*

---

## Reading guide

This document describes **how Augur learns what to trust**. It does not describe how to predict the future. Calibration is about weighing input data based on whether the signals each source produced turned out to be the ones worth weighing.

Cross-references:
- Replay-mode architectural requirements → `augur-architecture.md` ("Replay mode" section)
- The pipeline being calibrated → `augur-signal-pipeline.md`
- The source registry being weighted → `augur-sources.md`
- Schema invariants that calibration must preserve → `augur-graph-schema.md`

---

## What calibration is, sharply

Calibration is the process of **tuning source weights and lens parameters** using retroactive replay of historical sources, by measuring how well each source's signals corresponded to causal chains that actually unfolded.

It is not:

- A prediction test. Augur is not being scored on whether projections came true.
- A graph-quality test. The graph is a byproduct of calibration, not its target.
- A model evaluation. Calibration evaluates *sources and lenses*, not the LLMs doing the extraction.

The cleanest framing: **calibration asks which sources, when read live at time T, produced signals that the world subsequently confirmed were worth weighing.**

This is a sharper goal than evaluating projection accuracy. Projection accuracy depends on many things (graph schema quality, lens design, anchoring logic, projection algorithm) that are difficult to isolate. Source-weight calibration is empirically tractable: you can run a source's historical output through the pipeline, watch how its signals fared over the subsequent weeks and months, and produce a single number that summarizes its track record.

---

## The replay-mode mechanic

The architectural prerequisite for calibration is replay mode (see `augur-architecture.md`). The core idea is a **time cutoff slider**: an effective system time setting such that all reads see only signals, events, payloads, and graph states that existed at or before that time.

### How the slider works

The system supports an `as_of` parameter on every read. Setting `as_of = 2026-03-15T00:00:00Z` means:

- The graph state returned is the graph as it existed at that timestamp.
- Tier A signal queries return only signals whose `content_timestamp` is ≤ the cutoff.
- LLM extraction calls during a replay run are sandboxed with system prompts instructing the model to reason as if the cutoff is the current date.
- Projection runs walk the graph state as of that timestamp.

### How replay differs from normal operation

In normal operation, ingestion fetches new payloads and timestamps them at the moment of fetch. In replay, ingestion is given a historical payload corpus with `content_timestamp` set to each item's original publication or observation time, and processed in chronological order with the slider stepping forward.

The pipeline stages themselves are the same. Anchoring, applier, disconfirmation — all the same logic. The only differences are:

- `content_timestamp` is sourced from the historical payload metadata, not from fetch time.
- LLM model selection prefers models with training cutoffs before the replay window (see "Look-ahead bias" below).
- Extraction prompts include explicit instructions to ignore knowledge post-dating the cutoff.
- Tier B graph update events are timestamped at `content_timestamp`, not at processing time.
- Disconfirmation passes and projections also run at the replay cutoff.

The replay can run faster than wall-clock time (a year of historical signal compressed into days of processing) or slower (deliberately stepping through specific events). Either is fine.

---

## Look-ahead bias and its countermeasures

The single biggest threat to calibration validity.

If the LLM doing extraction "in March 2026" already knows from its training data what happened by August 2026, its extractions will be retroactively shaped by that knowledge. Signals will appear more prescient than they would have been at the time, sources will look more accurate than they were, and the calibration's source weights will be corrupted.

### The four countermeasures

#### 1. Model training cutoff selection

Where possible, use LLMs whose training cutoff predates the replay window's end date by at least 6-12 months. For a calibration run covering 2024-2026, ideally use models trained through 2023.

This is the strongest single countermeasure. A model genuinely lacking the relevant post-cutoff data cannot leak it into extractions.

The trade-off is capability: older models are often weaker at structured extraction. The acceptable performance floor is calibration-specific. A weaker model that doesn't cheat is better than a stronger model that does.

OpenRouter exposes per-model training cutoff metadata; the model-selection layer in the LLM client (see `augur-architecture.md`) supports filtering by cutoff date for calibration runs.

#### 2. Prompt sandboxing

Every extraction prompt during a replay run includes explicit instructions framing the model as operating at the cutoff date:

> *"You are reading this article on [historical date]. The current date for your purposes is [historical date]. Do not reason about or reference events that occurred after this date, even if you know about them. Produce signals based only on what could be known to a reader on this date."*

This is necessary but not sufficient. Models honor the instruction imperfectly. Models with training cutoffs after the replay window may still leak post-cutoff knowledge despite the instruction. The instruction is a backstop, not the primary defense.

#### 3. Output spot-checking

For a sample of extractions, manually review whether the extracted signal could plausibly have come from the source content alone, or whether it betrays knowledge of subsequent events.

Signs of leakage to look for:
- The signal mentions specific consequences that the source article does not.
- The signal's confidence band is suspiciously high given the source's hedging.
- The signal anticipates a development that the article's framing doesn't support.
- The signal's reasoning section references events outside the source content.

Spot-checking is operator labor. It does not need to be exhaustive, but a representative sample (say, 1-2% of extractions) should be reviewed. Detected leakage triggers model swaps or prompt revisions.

#### 4. Cross-perspective sanity check

If a calibration run shows that a source's signals were astonishingly prescient on a specific event, check whether that prescience is reflected across the perspective pool. A single Reuters article that "predicted" the Iran-Israel conflict three weeks before it began is suspicious. The same prediction echoed across India, Gulf, and Russia perspective pools is more credible.

Suspicious patterns are flagged for operator review and the originating extractions are spot-checked.

### What leakage detection cannot do

These countermeasures reduce leakage but do not eliminate it. The honest position is that calibration source weights are *approximations* of what the source's true track record would be if the leakage problem could be solved completely. The approximation is useful, but it should not be treated as ground truth.

In practice, this means:

- Calibration weights are starting points, not final values.
- Ongoing live operation continues to tune weights based on actual forward signal.
- A source whose calibration weight differs significantly from its live-operation weight after several months is investigated for which value is more representative.

---

## The signal-survival metric

How calibration scores sources.

The unit of measurement is the **signal**, not the article. A source produces a signal (via lens extraction) when its content prompts a lens to emit a structured claim. Calibration scores each signal on its **survival** through the pipeline over the subsequent observation window.

### Signal outcomes

Every signal extracted during replay reaches one of these states by the end of the observation window:

| Outcome | Meaning | Score contribution |
|---|---|---|
| `anchored_strengthened` | Signal contributed to a graph edge that was subsequently strengthened by corroborating signals | Strong positive |
| `anchored_persistent` | Signal contributed to a graph edge that remained in the graph, neither strengthened nor weakened | Mild positive |
| `anchored_weakened` | Signal contributed to a graph edge that was later weakened by disconfirmation | Mild negative |
| `anchored_deprecated` | Signal contributed to a graph edge that was later marked deprecated as contradicted | Strong negative |
| `clustered_but_not_anchored` | Signal made it to Tier A and clustered with corroborating signals, but never reached anchoring | Neutral |
| `isolated_in_tier_a` | Signal entered Tier A but never clustered or anchored — extracted in isolation | Mild negative |
| `extraction_rejected` | Signal was extracted but failed schema validation or was duplicate | Operationally tracked, not scored |

The scoring weights themselves are configuration, tuned during the first calibration run by examining the distribution. Initial values:

```yaml
anchored_strengthened: +1.0
anchored_persistent: +0.3
anchored_weakened: -0.3
anchored_deprecated: -1.0
clustered_but_not_anchored: 0.0
isolated_in_tier_a: -0.2
extraction_rejected: 0.0
```

### Per-source score calculation

For each source, over a calibration window:

```
raw_score(source) = sum(outcome_score(s) for s in signals_from(source))
n_signals(source) = count(signals_from(source))
mean_score(source) = raw_score(source) / n_signals(source)
```

The **mean score** is the per-signal-quality measure. The **raw score** captures volume contribution.

Both matter:

- A source with high mean score and low volume is a high-quality niche source.
- A source with moderate mean score and high volume is a workhorse.
- A source with high volume and low mean score is a noise generator that should be downweighted or removed.

### Translating to source weight

The source weight in the registry is updated using mean score, smoothed against the source's tier baseline and its prior calibration weight:

```
new_weight = 0.5 * tier_baseline + 0.3 * prior_weight + 0.2 * mean_score_scaled
```

Where `mean_score_scaled` is the source's mean score mapped from the typical range (~-0.5 to ~+1.0) into the tier's allowed weight range.

The formula is deliberately conservative: 50% weight to the tier baseline prevents calibration from making large jumps based on small samples. As more calibration data accumulates, the formula can shift toward favoring empirical mean score.

### What this metric captures and doesn't

It captures:

- Whether a source's signals tended to be the ones worth weighing.
- Whether a source produced signals that survived the disconfirmation discipline.
- Whether a source's signals tended to corroborate or contradict other signals.

It does not capture:

- Whether the source was right about specific events (that would require ground-truth event lists, which is a different and harder problem).
- Whether the source's signals were *interpretively* useful even when wrong (some sources surface important questions even when their answers are off).
- Whether the source's exclusion from a topic was itself a signal (silence in some sources is meaningful; this metric is blind to it).

The metric is a useful approximation, not a complete evaluation. It is sharpened by operator review of the top-scoring and bottom-scoring sources at the end of each calibration run.

---

## Lens calibration

The same machinery scores lenses, not just sources.

For each lens:

```
mean_score(lens) = sum(outcome_score(s) for s in signals_from(lens)) / count(signals_from(lens))
```

A lens with a low mean score is producing signals that don't survive — it's a noise generator. A lens with a high mean score but low signal volume is too narrowly scoped. A lens with moderate score and high volume is doing its job.

Lens calibration outputs:

- **Confirm or revise lens scope.** A lens producing many `isolated_in_tier_a` signals is finding things no other lens corroborates — possibly noise, possibly real but too far from other lenses' priors. Operator judgment decides.
- **Confirm or revise lens prompts.** A lens producing many `anchored_deprecated` signals is extracting confident-sounding claims that don't survive disconfirmation. Prompt revision required.
- **Confirm or revise the lens catalog.** A lens that consistently scores poorly across multiple calibration windows is a candidate for removal. A topic area that's not being covered by any lens (visible by examining what kinds of payloads produce zero signals) is a candidate for a new lens.

---

## Time window selection

What period to replay matters.

### Selection criteria

A good calibration window has:

1. **Sufficient duration** for causal chains to play out. Most useful causal chains in Augur's scope (commodity flows → prices → consumption → political response) take months to unfold. A 6-12 month window is the minimum; 12-24 months gives much more signal.
2. **A clear arc** with identifiable precursors and outcomes. Specific crises (energy shocks, financial events, conflict periods) produce cleaner calibration data than diffuse periods.
3. **Adequate source availability** for historical sources. Some sources have good public archives (Reuters, Bloomberg via partner archives, USGS, FRED). Others are harder to retrieve historically.
4. **Sufficient distance from training cutoffs** of the models being used. The further back the window from current model training cutoffs, the smaller the look-ahead bias risk.

### Recommended initial calibration runs

Three calibration windows, run in order:

#### Run 1: A specific bounded crisis (proof of concept)

A well-documented crisis with a clean arc, old enough that model training is somewhat diffuse on it. Candidates:

- **The 2022-2023 European energy crisis** — clean precursors (Russian gas dependency), clear arc (Nord Stream sabotage, LNG buildout, demand destruction, price normalization), well-documented. Most relevant to Nordic/EU signal.
- **The 2020-2021 COVID supply chain crisis** — global, cross-sector, with many measurable outcomes. Risk: chaotic, hard to assign clean signal-to-outcome links.
- **The 2022 Russian invasion of Ukraine and its commodity shocks** — clear arc, abundant signal, multiple downstream chains (wheat, fertilizer, energy, defense). Risk: most models have heavy training data on this.

Recommendation: **2022-2023 European energy crisis** for run 1. Cleanest arc, manageable scope, directly relevant to Nordic perspective.

#### Run 2: A recent multi-domain window

After run 1 has tuned the basic pipeline, run a second calibration on a more recent multi-domain window — say, 2024-2025. This is closer to current and stresses the look-ahead bias countermeasures more, but also produces source weights closer to what will be relevant in live operation.

#### Run 3: Cross-perspective stress test

A window where perspective divergence is the central feature — for instance, the 2022-2024 period of US-China decoupling, with strong divergent narratives across US/EU, China, and Southeast Asia perspectives. This run tests the `narrative_divergence` lens and the cross-perspective convergence detection, which earlier runs may not have stressed.

### Window parameters

Each calibration run has:

```yaml
calibration_run_id: <UUID>
window_start: <date>
window_end: <date>
observation_extension: <days; how long after window_end to track signal survival>
source_subset: <list of source_ids included; defaults to "all">
lens_subset: <list of lens_ids included; defaults to "all">
model_overrides: <which models to use, overriding defaults>
sandbox_prompt_template: <which sandbox instruction to use>
notes: <operator notes on intent>
```

The `observation_extension` is important: a signal extracted on the last day of the window can't be scored unless the system continues to observe what happens to it for some weeks afterward. A 60-90 day extension is typical.

---

## What success looks like for a calibration run

A calibration run succeeds when:

1. **Source weights stratify meaningfully.** The distribution of weights after calibration is not flat. Some sources rise, some fall, with reasoning that holds up to operator review.
2. **Lens mean scores reveal weak lenses.** At least one lens scores poorly enough to prompt prompt revision or scope adjustment. If all lenses look great, the scoring is probably not sensitive enough.
3. **Detected leakage is below threshold.** Spot-checks find no more than ~5% of sampled extractions showing look-ahead bias. Higher rates trigger model or prompt revision and a re-run.
4. **The operator's intuition aligns roughly with the data.** Sources the operator already trusted should generally score well; sources the operator was skeptical of should generally score poorly. Surprises in either direction are interesting and worth investigating.
5. **A small number of false positives are identified.** Sources or lenses that the operator would have rated well but that scored poorly empirically — these are the highest-value findings, the cases where calibration corrected a prior.
6. **A small number of false negatives are identified.** Sources or lenses that scored well empirically despite operator skepticism — equally high-value findings.

A calibration run fails when:

1. The scores converge toward uniform — no stratification means the metric isn't discriminating.
2. Leakage detection finds extensive look-ahead bias and the countermeasures can't reduce it. The calibration must be re-run with different model selection or a different window.
3. The signal volume is too low to produce statistically meaningful per-source scores. Either the window is too short or the source subset is too narrow.
4. Operator review reveals that high-scoring signals are systematically the kinds of signals the operator finds unhelpful (e.g., the metric rewards confident-sounding noise). The scoring weights need revision.

---

## Calibration outputs

A completed calibration run produces:

1. **Updated source weights** in the source registry.
2. **Updated lens scores** for each lens.
3. **A calibration report** for the operator: summary statistics, sources whose weights changed most, lenses scoring poorly, leakage detection results, notable signals (high positive, high negative, anomalous).
4. **A list of flagged sources** for operator review: weights that changed by more than a threshold, sources whose volume vs quality is inconsistent, sources whose scores diverge across perspective pools.
5. **A list of flagged lenses** for prompt revision or scope adjustment.
6. **The replay graph state itself**, preserved as a historical snapshot. The graph as it would have been seen at the calibration window's end. Useful for retrospective analysis and for visualization of how the graph evolved during replay.

The graph state from a calibration run is not promoted to be the live graph. It's a snapshot for analysis. Live operation starts from a separate initialization (which itself may use calibrated weights but begins ingestion from current sources at current time).

---

## Ongoing calibration during live operation

The same scoring machinery runs continuously during live operation, not just during dedicated calibration runs.

Every signal extracted in live operation enters the same outcome tracking. After enough time has passed for its outcome to resolve (signals are scored ~60-90 days after extraction, depending on the signal type's decay characteristics), it contributes to the source's and lens's running scores.

### Quarterly weight updates

The operator-facing calibration UI surfaces a quarterly report:

- How each source's running score has trended.
- Which sources have proposed weight changes large enough to warrant operator review.
- Which lenses have proposed scope or prompt revisions.

The operator either approves these updates (committing to the registry) or rejects them with reasoning logged. Weight updates never apply automatically — the operator is always in the loop for changes to source-level configuration.

### When to run a fresh dedicated calibration

Beyond ongoing live calibration, dedicated retroactive runs are useful periodically:

- After significant changes to the lens catalog or schema.
- After adding a new perspective pool or substantial new source set.
- When ongoing calibration produces ambiguous results — running a controlled replay of a known window can disambiguate.
- When a specific historical event is worth restudying with the current pipeline (e.g., a year after a major event, replay it with the current lens catalog to see if the system would have detected it earlier).

---

## What calibration deliberately does not try to do

- **No prediction validation.** Calibration does not ask whether projections came true. That's a separate evaluation, deferred until projection itself is mature.
- **No ground-truth event labeling.** Calibration does not require the operator to label specific events as "the truth." Signal survival is the metric; survival is determined by downstream graph operations, not by external ground truth.
- **No automatic source admission.** Calibration scores existing sources; it does not propose new sources. SearXNG-discovered candidates go through the operator admission process (see `augur-sources.md`), not through calibration.
- **No model evaluation.** Calibration evaluates sources and lenses. Model quality is evaluated separately, via Langfuse cost-and-latency dashboards and via spot-checks on extraction quality.
- **No real-time scoring.** Signal outcomes resolve over weeks. Scoring is necessarily lagged. The system does not pretend to know in real time whether a fresh signal is "good."

---

## Practical first calibration run

Stepping out of methodology for a moment, here is the concrete first run as it should be structured:

**Window:** September 2022 - June 2023, European energy crisis arc.

**Observation extension:** 90 days after window end (through September 2023).

**Source subset:** Initial registry minus any sources whose historical archives aren't accessible.

**Lens subset:** All seven lenses.

**Model overrides:**
- Extraction: a model with a 2022-or-earlier training cutoff, preferring cost-efficient models given the volume of replay extraction.
- Anchoring: a stronger model with the same cutoff constraint.
- Disconfirmation: strongest available within the cutoff constraint.

**Sandbox prompt:** Standard template (specified above), with date stepping forward weekly through the window.

**Expected duration:** Several days of processing time given the historical payload volume. Tunable by parallelism and rate-limit headroom on OpenRouter.

**Success criteria for run 1 specifically:**
- The pipeline completes end-to-end without major failures.
- Source weight stratification emerges (some sources move significantly, others don't).
- The European-energy graph at window end is recognizable as a reasonable representation of the crisis.
- Lens scores reveal at least one weak lens for prompt revision.
- Leakage detection passes spot-check threshold.

This first run is as much a system validation as a calibration. If the pipeline can handle a known historical arc and produce a recognizable graph at the end, the core architecture is sound.
