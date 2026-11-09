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

## Hypotheses to test next (none verified)

- The synthesis prompt's category example and the skills push a 7B model toward `application`. Check by listing the prompt's category guidance and by trying a stronger model.
- Citation failures come from the model not emitting evidence ids in its hypotheses. Check with the stored `rationale` and raw hypothesis output.
- "Blame the latest change" comes from the model, from the `deploy`-flavoured skills, or from the change events being the most legible evidence. Check by removing the decoy change from `dependency_outage` scenarios.
- A larger model, or enabling the runbook knowledge base, changes the picture. Run the same suite after changing the diagnosis model setting to isolate this.

## Next steps

1. Run again under scoring version 2 with new file names (`rca_agent_run2.*`). The old checkpoint is ignored automatically because the scoring version is part of its key. Then repeat for a measured spread.
2. Inspect `rationale` for the 9 no-citation scenarios.
3. Decide whether to treat low confidence (at most 0.4) as an official abstention signal. It is reported as a separate, pre-declared metric, so the abstention definition is not changed after seeing results.
4. Only then change prompts or skills, re-run, and compare on the same suite hash.
