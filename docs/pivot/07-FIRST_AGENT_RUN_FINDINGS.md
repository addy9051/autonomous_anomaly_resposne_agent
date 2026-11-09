# First Agent Run: Findings

Run: `python -m evals.rca_bench run --solver agent` on `synthetic_v0.jsonl` (40 scenarios, suite sha256 `b13d07f21a0d...`), model `qwen2.5-7b-instruct` on the configured Modal endpoint, no runbook knowledge base available (the agent logged `rag_kb_unavailable` for every scenario), 40 of 40 scenarios completed, 0 run errors, mean 131 s per scenario. **One run, synthetic data, a 7B model: treat every number as provisional.**

## Results (scoring version 1)

| Metric | Agent | Best heuristic (`alert_service_recent_change`) | Plan target ([05](./05-POSITIONING_AND_VALIDATION_PLAN.md)) |
|---|---|---|---|
| Top-1 change accuracy (35 answerable) | 0.600 | 0.571 | clearly above the heuristic |
| Category accuracy | 0.514 | 0.657 | n/a |
| Abstains when unanswerable (5 cases) | 0.000 | 0.000 | at least 0.80 |
| Confident (>=0.7) errors | 0.275 | 0.500 | at most 0.10 |
| Calibration error (ECE) | 0.169 | 0.300 | at most 0.15 |
| Valid citations | 1.000 | 1.000 | n/a |
| Correct answers citing real support | 0.895 | 1.000 | n/a |

Per fault class (right answer, or abstained when unanswerable): `bad_deploy` 0.6, `bad_deploy_decoy_later` 1.0, `resource_limit_change` 1.0, `config_change` 0.8, `same_service_later_noop` 0.4, `dependency_outage` 0.2, `traffic_surge` 0.2, `insufficient_evidence` 0.0.

## Caveat: the top-1 figure is an upper bound

Scoring version 1 ranked every change the agent implicated in chronological order. In `bad_deploy_decoy_later` and `resource_limit_change` the generator makes the culprit the oldest change, so an agent that blamed several changes was credited with the culprit first. Seven of the 21 correct answers implicated more than one change (for example `bad_deploy_decoy_later-01` listed four). Their credit may be an ordering artifact, so version 1 top-1 is between about **0.40 and 0.60**, not 0.60. The report from this run cannot be rescored because it did not keep which hypothesis cited which change.

Scoring version 2 (in the harness now) treats changes in the root cause and top hypothesis as one tied group, and ties count against the solver. Baseline results are unchanged under version 2. Reports now carry `scoring_version`, and `summarize` warns when versions are mixed.

## What the run shows

1. **The agent blames a change even when none is responsible.** In the 10 scenarios with no culprit change (`dependency_outage`, `traffic_surge`), it implicated a change in 8. The two it got right cited no evidence at all.
2. **It predicted `application` for all 40 scenarios,** never `infrastructure`, `external`, `database` or `unknown`. Category accuracy 0.514 is just the share of scenarios whose truth is `application` (18 of 35 answerable). The same happens with `resource_limit_change`: the culprit was credited 5 of 5 times but labelled `application`.
3. **It never abstains on unanswerable cases** (0 of 5), because it never reports `unknown`. Low stated confidence is a weak substitute: confidence was at most 0.4 on 2 of 5 unanswerable cases and 10 of 35 answerable ones. Those 0.4 values come from the deterministic cap applied when the agent cites no supporting evidence, so they track missing citations, not unanswerability.
4. **In 9 scenarios the agent cited no evidence and implicated no change.** Six were answerable and counted as misses (`bad_deploy-04`, `bad_deploy-05`, `same_service_later_noop-02/03/05`, `config_change-02`). Whether the model named the right change in prose without citing it, or failed to produce usable hypotheses, is unknown because the report did not store the root-cause text. Scoring version 2 stores it (`rationale`).
5. **Confidence is not calibrated enough yet.** 11 of 40 predictions were confident (0.7 or higher) and wrong. The deterministic caps help (they produce the 0.4 values), but nothing caps confidence when the model cites plausible but wrong evidence.

## Against the proposed pass criteria

| Hypothesis | Result on this run |
|---|---|
| H2: attribution beats simple heuristics | Not shown. 0.60 is at most marginally above 0.571 and may be lower under version 2. |
| H3: ECE at most 0.15 and confident errors at most 10% | Not met (0.169 and 27.5%). |
| H4: abstains on at least 80% of unanswerable | Not met (0%). |

This is what the benchmark is for: it shows where the agent is weak before any claim is made. It does not show the agent is bad in general. The suite is small and synthetic, the model is a 7B open-weight model, the runbook RAG was off, and the replay encodes changes into metric names as a workaround (see [06](./06-RCA_BENCHMARK.md)).

## Run 2 (scoring version 2, L40S, concurrency 4, runbook search skipped)

Same suite and model. The whole suite took about 8 minutes (mean 45 s per scenario under contention) instead of about 80. Run 1 and run 2 use different scoring versions, so they are **not directly comparable**, and one run each does not measure run-to-run spread.

| Metric | Run 2 |
|---|---|
| Top-1 change accuracy (35 answerable) | 0.657 (23/35; 95% interval about 0.49-0.79) |
| Best heuristic, same suite | 0.571 (20/35; interval about 0.41-0.72) |
| Category accuracy | 0.514 |
| Abstains when unanswerable | 0.000 |
| Low confidence (at most 0.4) when unanswerable / answerable | 0.600 (3/5) / 0.200 (7/35) |
| Confident (0.7 or higher) errors | 0.275 (11/40) |
| Calibration error (ECE) | 0.130 |
| Correct answers citing real support | 0.826 |
| Run errors | 0 |

Per fault class: `bad_deploy`, `bad_deploy_decoy_later`, `config_change`, `same_service_later_noop` all 5/5; `resource_limit_change` 3/5; `dependency_outage`, `traffic_surge`, `insufficient_evidence` all 0/5.

### What run 2 shows

1. **Split by whether a change is to blame.** On the 25 scenarios where a listed change is the culprit, the agent is right on 23 (the heuristic: 20). On the 15 where no change is the answer (10 with no culprit, 5 unanswerable) it is right on **0**. Every one of the 11 confident errors is in those 15.
2. **The one place it clearly beats the heuristic is `same_service_later_noop` (5/5 vs 0/5),** where a harmless later change on the same service must be ruled out using the error frame and changed files. On the other classes the heuristic ties it. With 5 scenarios per class this is a hint, not a result.
3. **It always blames a change.** Even with an upstream dependency down or a traffic surge, it names a recent deployment as the cause (for example `dependency_outage-01` blames a base-image bump). It reported `application` for all 40 scenarios again in this run, so the category finding is consistent across both runs.
4. **Low confidence weakly tracks unanswerable cases** (3/5 vs 7/35). With 5 cases this could be chance.
5. **The two `resource_limit_change` misses are partly a measurement issue.** In `resource_limit_change-04` and `-05` the agent's conclusion correctly describes the memory-limit reduction, but it cited no evidence and did not write the change id, so the scorer cannot credit it. Crediting prose would give up to 25/35 (0.714). The harness deliberately requires a citation, because an uncited claim is exactly what SREs cannot check.
6. **Seven answerable scenarios had no citations at all,** several still credited because the conclusion text names the change id.

### Against the proposed pass criteria (run 2)

| Hypothesis | Result |
|---|---|
| H2: attribution beats simple heuristics | Not established. 0.657 vs 0.571 with overlapping intervals. The clear difference is one class of 5. |
| H3: ECE at most 0.15 and confident errors at most 10% | ECE met (0.130); confident errors not met (27.5%), all from cases where no change is to blame. |
| H4: abstains on at least 80% of unanswerable | Not met (0%). Low confidence on 60% is a weak partial signal. |

## A/B experiment results: `evidence_discipline` is rejected

3 runs per configuration, `concurrency=12`, `qwen2.5-7b-instruct`, scoring version 2, run on both suites (the dev suite the variant was written against, and the unseen holdout suite). Values are mean (min-max) across the 3 runs.

| Metric | Dev baseline | Dev variant | Holdout baseline | Holdout variant |
|---|---|---|---|---|
| Top-1 change accuracy | 0.619 (0.571-0.686) | **0.352** (0.229-0.429) | 0.610 (0.600-0.629) | **0.333** (0.257-0.371) |
| Confident (0.7 or higher) errors | 0.317 | 0.050 | 0.317 | 0.083 |
| Calibration error (ECE) | 0.167 | 0.176 | 0.189 | 0.178 |
| Abstains when unanswerable | 0.000 | 0.000 | 0.000 | 0.000 |
| Correct answers citing real support | 0.933 | 0.581 | 0.948 | 0.474 |
| `dependency_outage` accuracy | 0.200 | 0.600 | 0.400 | 0.667 |
| `insufficient_evidence` accuracy | 0.000 | 0.000 | 0.000 | 0.000 |

**Decision under the rule set in advance (06): reject.** The rule was to adopt only if the variant improved the no-culprit and unanswerable scenarios on the holdout without reducing top-1 on scenarios that have a culprit, by more than the baseline's own spread. It did improve `dependency_outage`, but top-1 fell from about 0.61 to about 0.33, far beyond the baseline spread of about 0.06, and the unanswerable scenarios did not improve at all.

### Why it failed (measured, by scenario type, dev / holdout)

| | Baseline | Variant |
|---|---|---|
| Scenarios WITH a culprit: top-1 | 0.80 / 0.76 | 0.32 / 0.28 |
| Scenarios WITH a culprit: implicated no change at all | 0.19 / 0.19 | 0.67 / 0.71 |
| Evidence records cited per prediction | 2.5 / 2.7 | 0.6 / 0.5 |
| Mean stated confidence (culprit scenarios) | 0.72 / 0.73 | 0.47 / 0.46 |
| Scenarios with NO culprit: correctly implicated none | 0.17 / 0.23 | 0.43 / 0.47 |

The rules made the 7B model stop blaming changes and stop citing evidence everywhere, not just where it should. It got better at "no change" cases (0.2 to 0.45) at the price of losing the culprit in two thirds of the cases where one exists. It also hedged on confidence across the board (low confidence on 82-93% of predictions), which is why confident errors fell, but that is a side effect of not committing, not better calibration.

### What the 3-run baseline establishes

- **Results are stable.** Top-1 is 0.619 on dev and 0.610 on holdout, with a spread of about 0.06, so the holdout confirms the dev numbers rather than exposing instance-level overfitting. (It cannot expose overfitting to the scenario types, which both suites share.)
- **The agent is roughly tied with the best simple heuristic overall** (0.571 on both suites), not clearly ahead. The heuristic scores 1.0 on four fault classes by construction of the generator, where the alerting service's latest change is always the culprit. The agent is **worse than the heuristic on those four classes** (0.53-0.93) and **clearly better only on `same_service_later_noop`** (0.80-0.87 vs 0.0), where reading the error frame and changed files is required. This suite favours the heuristic, so real incidents may differ.
- **Category is degenerate:** `application` for 117 of 120 predictions on dev and 119 of 120 on holdout.
- **The agent never abstains** (0 of 15 unanswerable, in every run) and never answers `unknown`.
- **Calibration is not met:** ECE 0.167-0.189 against a target of 0.15 or lower, and confident errors 0.317 against a target of 0.10 or lower.

### Conclusions

1. Prompt text alone is the wrong lever for this model. Four rules in one prompt shifted behaviour everywhere, not selectively.
2. The weaknesses that matter (no abstention, always blaming a change, degenerate category) are properties of how this 7B model decides, so the fix should come from structure the code controls, not from more instructions.

## Deterministic temporal rule (offline rescoring of the 3 baseline runs)

The rule, in `agents/diagnosis/attribution.py`: **a change that landed after the earliest symptom cannot be the cause.** It only removes candidates; it never proves one. Because it is a pure function of the scenario, it can be applied to saved predictions with no model calls (`python -m evals.rca_bench rescore`), which gives exactly what the `temporal` attribution mode would produce from the same diagnoses. Checked invariant: it removes a true culprit in **0 of 80** scenarios across both suites (and removes something in many, so the check is not vacuous).

| Metric (mean, min-max over 3 runs) | Dev baseline | Dev + rule | Holdout baseline | Holdout + rule |
|---|---|---|---|---|
| Top-1 change accuracy | 0.619 (0.571-0.686) | **0.819** (0.743-0.886) | 0.610 (0.600-0.629) | **0.800** (0.771-0.829) |
| Confident (0.7 or higher) errors | 0.317 | 0.150 | 0.317 | 0.167 |
| Calibration error (ECE) | 0.167 | 0.131 | 0.189 | 0.168 |
| `dependency_outage` accuracy | 0.200 | 0.800 | 0.400 | 0.867 |
| `traffic_surge` accuracy | 0.133 | 0.867 | 0.067 | 0.933 |
| `insufficient_evidence` accuracy | 0.000 | 0.000 | 0.000 | 0.000 |
| Classes with a culprit | unchanged | unchanged | unchanged | unchanged |

**By the pre-declared decision rule this passes:** on the holdout it greatly improves the no-culprit cases without reducing top-1 on cases with a culprit, by far more than the baseline spread (about 0.06).

### The fair comparison: give the simple heuristic the same rule

| Top-1 (both suites) | Without the rule | With the rule |
|---|---|---|
| `alert_service_recent_change` heuristic | 0.571 | 0.714 |
| Agent (baseline diagnosis) | 0.619 / 0.610 | 0.819 / 0.800 |

So about 0.14 of the heuristic's gain is the rule itself, and the agent still adds about 0.09-0.10 on top. The agent's value is concentrated where no listed change is responsible: it scores 0.80-0.93 on `dependency_outage` and `traffic_surge` with the rule against 0.0 for the heuristic, because the heuristic always names some older change, while the agent mostly does not.

### Caveats, and they are large

- **The suite is built so the rule works.** My generator puts the recent decoy changes after the symptom onset in the classes where it matters. The rule exploits that structure. Treat the size of the gain as an upper bound for this suite, not a prediction for real incidents.
- **It needs real onset timestamps.** The rule uses the earliest symptom time. Today's adapters return the latest sample of a metric, not when it first went abnormal, so in production the rule would misfire until onset detection (the time a metric first breached its baseline) exists. That is the next piece of real work, and it is not done.
- **It does not touch the weaknesses that need judgement:** abstention is still 0 of 15, and the category is still `application` for about 97% of answers.
- Three runs per suite, one 7B model.

## Decomposed attribution (`--attribution decompose`): rejected

Live runs, 3 per suite, `qwen2.5-7b-instruct`, same suites and scoring as above. "Culprit classes" are `bad_deploy`, `bad_deploy_decoy_later`, `config_change`, `resource_limit_change`.

| Holdout (mean of 3) | baseline | temporal (offline) | decompose | decompose + temporal (offline) |
|---|---|---|---|---|
| Top-1 | 0.610 | **0.800** | 0.667 | 0.752 |
| Confident (0.7 or higher) errors | 0.317 | 0.167 | 0.075 | **0.017** |
| Abstains when unanswerable (n=5) | 0.0 | 0.0 | 0.6 | 0.6 |
| Abstains when answerable | 0.0 | 0.0 | 0.248 | 0.248 |
| Coverage | 1.00 | 1.00 | 0.71 | 0.71 |
| ECE | 0.189 | 0.168 | 0.215 | 0.303 |
| Brier | 0.277 | 0.235 | 0.205 | 0.186 |
| `config_change` | 0.867 | 0.867 | **0.400** | 0.400 |
| `dependency_outage` | 0.400 | 0.867 | 0.267 | 0.267 |
| `traffic_surge` | 0.067 | 0.933 | **0.000** | 0.600 |
| `insufficient_evidence` | 0.000 | 0.000 | 0.600 | 0.600 |

The dev suite agrees on direction (top-1 0.619 baseline, 0.819 temporal, 0.676 decompose, 0.733 decompose plus temporal; `config_change` 0.933 to 0.400; `traffic_surge` 0.133 to 0.000).

**Verdict: not adopted.** Applying the pre-declared rule per class, decompose cuts `config_change` (a culprit class) by 0.47 on both suites, consistently across all 3 runs, and does not help the no-culprit classes except `insufficient_evidence`. Against the free deterministic rule it is worse: decompose plus temporal has lower top-1 than temporal alone (0.752 vs 0.800 holdout, 0.733 vs 0.819 dev), at the cost of an extra model call and a 25% false-abstention rate on answerable scenarios. A reading that averages across culprit classes would show decompose ahead (holdout 0.85 vs 0.73), because it gains on `bad_deploy` and `resource_limit_change`; the rule was written as "no loss on culprit scenarios", and a 0.47 loss on one class is a loss.

**Why it fails (inspection of holdout run 1, the `config_change`, `dependency_outage`, `traffic_surge` and `insufficient_evidence` scenarios, 20 in total):** `cannot_tell` is used indiscriminately. It abstained on 3 of 5 `config_change` scenarios, which have a culprit; on 4 of 5 `dependency_outage`; on 2 of 5 `traffic_surge`; on 3 of 5 `insufficient_evidence`. The model chose `none` once in those 20. It treats "cannot tell" as the answer whenever it is unsure, so abstention does not track whether the question is answerable.

**The abstention gain is not reproducible.** Abstention on unanswerable scenarios was 0.6 on the holdout but 0.2 on dev, and abstention on answerable scenarios was about 0.25 on both. With 5 unanswerable scenarios per suite this is noise-sized evidence, not a result.

**What did help, and is worth keeping in mind:** confident errors fall to 0.017 and Brier improves (0.186 vs 0.235 for temporal alone), because disagreement between the narrow decision and the full diagnosis caps confidence. The cost is under-confidence (ECE 0.303) and answering only 71% of scenarios. Two answers that disagree is a usable uncertainty signal; the decomposed answer itself is not a usable attribution at this model size.

**Category accuracy equals top-1 exactly in the plain decompose runs (0.676 and 0.667). Do not read the new category spread (`infrastructure` and `external` now appear) as better categorisation.** It tracks the attribution outcome, so it is not independent evidence.

**Current best configuration on this benchmark: baseline diagnosis plus the deterministic temporal rule.** Its caveats above still apply (generator-built decoys, no real onset timestamps).

## Harness variant: keep the baseline attribution, cap confidence when a second question disagrees

Chosen after seeing the decompose results, so it was **not** pre-declared; treat it as a hypothesis that still needs a fresh holdout. Scored offline with `python -m evals.rca_bench.disagreement` (no model calls). It takes the baseline answer with the temporal rule applied and caps confidence at 0.4 when the narrow question (`decompose`, temporal rule applied) names a different change. It never changes which change is named and never abstains.

| Mean of 3 | Dev: temporal | Dev: + cap | Holdout: temporal | Holdout: + cap |
|---|---|---|---|---|
| Top-1 | 0.819 | 0.819 | 0.800 | 0.800 |
| Confident (0.7 or higher) errors | 0.150 | **0.075** | 0.167 | **0.025** |
| Brier | 0.207 | 0.196 | 0.235 | 0.189 |
| ECE | 0.131 | 0.136 | 0.168 | 0.145 |

The two questions disagreed on about 11 of 40 (dev) and 14 of 40 (holdout) scenarios per run. Top-1 is unchanged by construction, so this is purely a confidence result. Caveats: run k of the baseline is paired with run k of `decompose`, which are independent generations, so this approximates the live harness; it costs one extra short model call per scenario if run live; abstention is still 0 and the category is still degenerate.

## Confirmation of the confidence cap on a fresh seed (seed 2, live)

Decision rule from [06](./06-RCA_BENCHMARK.md), written before the runs. 3 live runs per configuration, same 7B model; `temporal` is the baseline runs with the temporal rule applied offline.

| Seed 2 (mean, min-max) | baseline | temporal | temporal_cap (live) |
|---|---|---|---|
| Top-1 | 0.667 (0.629-0.743) | 0.857 (0.800-0.886) | 0.800 (0.686-0.914) |
| Confident (0.7 or higher) errors | 0.258 (0.200-0.325) | 0.117 (0.100-0.125) | **0.025 (0.025-0.025)** |
| Brier | 0.237 | 0.197 (0.183-0.205) | **0.176 (0.168-0.192)** |
| ECE | 0.146 | 0.158 | 0.124 |
| Coverage | 1.00 | 1.00 | 1.00 |
| Correct answers citing real support | 0.888 | 0.890 | 0.952 |
| Low confidence (0.4 or lower), unanswerable / answerable | 0.27 / 0.27 | 0.27 / 0.42 | 0.80 / 0.54 |

**Verdict against the pre-declared rule: it passes.**
1. Confident errors fell by 0.092 (0.117 to 0.025); the worst `temporal_cap` run (0.025) is below the best `temporal` run (0.100). Pass.
2. Top-1 differs by 0.057, inside the 0.06 allowance, but only just. Pass, marginally (explained below).
3. Brier is better (0.176 vs 0.197), though the run ranges overlap (0.168-0.192 vs 0.183-0.205). Pass, modestly.

**The top-1 gap is sampling noise in the base diagnosis, not the cap.** The cap runs ranged 0.686 to 0.914. In the worst run (run 1), 7 scenarios were wrong only in that run. All 7 had no named suspect: the diagnosis in that run blamed only changes that landed after the symptoms (so the temporal rule removed them), or only the late decoy. Three independent runs of a stochastic pipeline are a thin basis for a top-1 comparison; compare the cap on confidence, which is what it is meant to change.

**What the cap does, in numbers (answerable scenarios, 3 runs pooled):**
- High-confidence answers (above 0.4): 48 of 48 correct with the cap, against 58 of 61 (0.95) for `temporal` alone. No answerable scenario had a confident error with the cap.
- Low-confidence answers: 63% of them were still correct, so the flag is not selective. It marks 54% of answerable answers, but it contains every answerable error.
- The one remaining confident error per run (0.025 = 1 of 40) is an `insufficient_evidence` scenario answered with high confidence. Four of the five unanswerable scenarios are low-confidence with the cap (0.8, against 0.27 without it).

**Exploratory, not pre-declared: the cap without the temporal rule** (offline, from the saved runs; pairs run k of the baseline with run k of `decompose`, so it is only an approximation). The temporal rule cannot run in production until real onset timestamps exist, so this checks the cap does not depend on it:

| Mean of 3 | Dev: baseline | Dev: + cap | Holdout: baseline | Holdout: + cap |
|---|---|---|---|---|
| Top-1 | 0.619 | 0.619 | 0.610 | 0.610 |
| Confident errors | 0.317 | 0.100 | 0.317 | 0.083 |
| Brier | 0.237 | 0.181 | 0.277 | 0.183 |

**Conclusion:** two independent-model-call answers that agree are a usable precision signal; their disagreement is a usable "do not trust this" signal. This is a harness change on the same 7B model, with no new model weights. It does not fix the main weaknesses: abstention is still 0 (and `insufficient_evidence` accuracy is 0), the category is still `application` for about 97% of answers, and the cap gives no help on which change to name.

**Limits:** one fresh seed from the same generator (see the template-overlap note in 06), three runs, one model, a synthetic suite built by me, and the result for the live cap on the dev and holdout suites was not run (only the offline estimate above).

## Hypotheses to test next (none verified)

- **Harness before model size.** The temporal rule (about +0.19 top-1) and the disagreement cap (about -0.14 confident errors) are both harness changes on the same 7B model. A larger model stays a useful single control run to measure how much the model matters, but it is not required to continue with harness work.
- **Judge each candidate change separately** (yes, no or unclear per change, with the harness aggregating: exactly one yes names it, none means no change, several means rank by deterministic signals). This addresses the observed `cannot_tell` over-use, where one three-way answer over the whole list was used as the default for any uncertainty.
- **Use token log-probabilities from the serving layer as the confidence signal** instead of the model's stated number, which is where the confident errors come from.
- **Fix the suite's blind spots:** add scenarios where the alerting service's latest change is not the culprit and where decoys land before the symptom onset, so the rule cannot do all the work.

## Next steps

1. Try the per-candidate judging variant (yes, no or unclear per change) and log-probability confidence, aimed at abstention and the `insufficient_evidence` class, which the cap does not touch.
2. Optionally, one run of the baseline with a larger diagnosis model as a control for how much the model matters.
3. Build onset detection in the telemetry adapters, so the temporal rule can run on real incidents.
4. Decide whether to treat low confidence (at most 0.4) as an official abstention signal. It stays a separate, pre-declared metric until then.
5. Grow the suite, remove the generator structure the rule exploits, and build the live fault-injection suite.
6. Only then wire the attribution step and rule into the production diagnosis graph, re-run, and compare.
