# RCA Benchmark Harness

Code: [`evals/rca_bench/`](../../evals/rca_bench). Tests: [`tests/evals/test_rca_bench.py`](../../tests/evals/test_rca_bench.py). Purpose: produce public, reproducible evidence of how well an investigator attributes incidents to changes, names the right category, states calibrated confidence and abstains when evidence is insufficient. See [05](./05-POSITIONING_AND_VALIDATION_PLAN.md) for why.

**Status: started.** The harness, a frozen synthetic suite, four baselines and an oracle run and are tested. One full agent run (40 scenarios, `qwen2.5-7b-instruct` on the configured Modal endpoint, about 131 s per scenario) is analysed in [07](./07-FIRST_AGENT_RUN_FINDINGS.md); it was scored under scoring version 1, whose top-1 figure is an upper bound. Scoring version 2 (current) counts blaming several changes as hedging, stores the agent's conclusion for auditing, and adds low-confidence and predicted-category metrics. No run under version 2 exists yet. Live fault-injected scenarios are not built yet.

## Run it

```powershell
# Compare baselines on the frozen suite (no LLM needed, seconds)
.venv\Scripts\python.exe -m evals.rca_bench run --solver oracle --solver most_recent_change --solver alert_service_recent_change --solver random --solver always_abstain

# Validate or regenerate the suite
.venv\Scripts\python.exe -m evals.rca_bench validate
.venv\Scripts\python.exe -m evals.rca_bench generate
```

### Full agent run

Run 1 took about 80 minutes sequentially (about 131 s per scenario on an L4). The measured split was about 33 s for the context summary, 48 s for the expert step, 44 s for synthesis, and about 12 s of failed runbook-search attempts, so almost all of it is three sequential LLM generations. Speed-ups that do not change what the agent does:

1. **Faster GPU for the chat model.** `infra/modal/vllm_app.py` now defaults the chat function to `L40S` (override with `CHAT_GPU`). Redeploy once; the URL does not change, so `.env` is unchanged:

```powershell
$env:CHAT_GPU = "L40S"
modal deploy infra/modal/vllm_app.py
```

2. **Keep the endpoint warm while iterating** (a cold start takes 8-10 minutes). This overrides the autoscaler without redeploying, and **bills GPU time continuously until you turn it off**. A redeploy resets it:

```powershell
python infra/modal/keep_warm.py on
# ... run experiments ...
python infra/modal/keep_warm.py off
```

3. **Parallel scenarios and no dead runbook search:**

```powershell
New-Item -ItemType Directory -Force evals\reports | Out-Null
.venv\Scripts\python.exe -m evals.rca_bench run --solver agent --warmup --concurrency 4 --skip-runbook-search `
  --checkpoint evals\reports\rca_agent_run2.jsonl `
  --out evals\reports\rca_agent_run2.json
```

- `--concurrency N` solves N scenarios at once. The endpoint accepts 16 concurrent requests, and decoding is memory-bound, so several requests share a GPU efficiently. Start with 4, then try 8. Per-scenario latency under concurrency includes contention, so compare total wall time, not per-scenario seconds, with older runs.
- `--skip-runbook-search` makes runbook lookups fail immediately. The knowledge base is empty in these runs, so the agent sees the same "no runbooks" outcome it saw before, without the wait.
- `--warmup` wakes the endpoint first and prints a heartbeat each minute. It gives up after `--warmup-timeout` seconds (default 1200) instead of waiting forever.
- Each scenario has a 900 s timeout (`--timeout`), counted from when it starts. A timeout is recorded as an error and counts as incorrect.
- Results are saved after every scenario. If the run stops for any reason, rerun the same command to resume. Scenarios that errored are retried.
- The report records the options used (concurrency, diagnosis model, LLM host, skip flag) so each result can be tied to its configuration.
- A scenario whose evidence was never requested by the agent is reported as an error, not scored. This guards against silently scoring a run on no evidence.

LLM output varies between runs. For a measured spread, repeat with `run3` and so on in the file names, then summarise:

```powershell
.venv\Scripts\python.exe -m evals.rca_bench summarize evals\reports\rca_agent_run2.json evals\reports\rca_agent_run3.json
```

`summarize` prints each metric (mean and min-max across runs), accuracy per fault class and the scenarios answered incorrectly. Use `--limit N` on `run` for a quick smoke test. Do not mix results from different scoring versions; `summarize` warns when you do.

Reports record the suite's SHA-256 so a number can always be tied to the exact scenarios.

## What is measured

| Metric | Meaning |
|--------|---------|
| Top-1 / Top-3 change accuracy | The culprit change is the first or in the first three suspects. When no listed change is to blame, the right answer is to implicate none. Answerable cases only. |
| Category accuracy | Root-cause category matches ground truth. |
| Abstains when unanswerable | On cases where evidence cannot justify a conclusion, the solver declines (category `unknown` for the agent). |
| Low confidence when unanswerable / answerable | Share of cases with confidence at most 0.4. A secondary signal, reported separately so the abstention definition is not changed after seeing results. |
| Abstains when answerable | Over-cautious abstention. |
| Confident errors | Share of all cases where confidence is at least 0.7 and the answer is wrong. |
| ECE and Brier | Calibration of stated confidence on answered cases. Correct means right, or abstained on an unanswerable case. |
| Coverage | Share of cases answered rather than abstained. |
| Valid citations | Cited evidence ids that exist in the scenario. |
| Correct answers citing real support | Correct attributions that cite at least one ground-truth supporting record. |
| Run errors | Runs that failed. Errors always count as incorrect. |

## The synthetic suite

[`suites/synthetic_v0.jsonl`](../../evals/rca_bench/suites/synthetic_v0.jsonl): 40 scenarios, 5 per fault class, generated deterministically by `generator.py` (seed 0) and frozen so results are comparable across code versions. A test fails if the file drifts from the generator.

| Fault class | Tests |
|-------------|-------|
| `bad_deploy` | Easy attribution to the only recent change |
| `bad_deploy_decoy_later` | Culprit is older than unrelated changes elsewhere |
| `same_service_later_noop` | A harmless later change on the same service must be ruled out using the error frame and changed files |
| `resource_limit_change` | Memory limit reduction causing OOM kills |
| `config_change` | Timeout, pool or retry setting |
| `dependency_outage` | No listed change is responsible; category `external` |
| `traffic_surge` | No listed change is responsible; saturation |
| `insufficient_evidence` | Telemetry unavailable, or two co-timed changes that cannot be told apart; correct behaviour is to abstain |

### Baseline results (synthetic suite)

| Metric | oracle | most_recent_change | alert_service_recent_change | random | always_abstain |
|---|---|---|---|---|---|
| Top-1 change accuracy | 1.000 | 0.286 | 0.571 | 0.286 | 0.000 |
| Category accuracy | 1.000 | 0.514 | 0.657 | 0.343 | 0.000 |
| Abstains when unanswerable | 1.000 | 0.000 | 0.000 | 0.000 | 1.000 |
| Confident errors | 0.000 | 0.750 | 0.500 | 0.575 | 0.000 |
| ECE | 0.100 | 0.550 | 0.300 | 0.497 | n/a |

The oracle reaches the ceiling, trivial strategies score far below it, and the heuristics are confidently wrong (they state a fixed 0.8). That shows the suite separates strategies. It says nothing yet about the agent.

## How the agent is presented with a scenario

`agent_solver.py` replays scenario evidence into the real `DiagnosisAgent` by patching the telemetry adapter and deployment lookups, so grounding, citation checks and confidence caps run unchanged. Differences from production that affect interpretation:

- **Evidence rendering finding:** the agent's citable evidence index (`build_evidence_index` in `agents/diagnosis/hypotheses.py`) prints only the metric name, value and time. Descriptions and tags, which would carry commit summaries and files, reach the synthesis prompt only through the LLM-written context summary from the gather step, which is lossy and cannot be cited by id. The harness works around this by encoding change ids, summaries, files and signal details into `metric_name`. Production needs a proper fix before change-aware RCA can be cited reliably (Phase 1 in [04](./04-PIVOT_OPTIONS_AND_RECOMMENDATION.md)).
- Replayed records are marked authoritative, not simulated; otherwise the agent's caps would hide its behaviour.
- Services named by signals are added to the alert's affected services.
- A suspect is a listed change the agent implicates by id in its root cause or hypotheses, or whose evidence record a hypothesis cites as support. The smoke test showed the agent paraphrases changes instead of writing ids, so citations are what count. Changes in the root cause and top hypothesis share one score and ties count against the agent (scoring version 2), so blaming several changes is hedging. Text that rules a change out by id would still implicate it.
- The agent abstains only by returning category `unknown`.

## Limits and honesty rules

- **Synthetic means authored by us.** Scenarios are modelled on Online Boutique but fabricated; the generator's patterns are learnable. Never quote a synthetic score as real-world accuracy.
- 40 scenarios give wide error bars. Report counts alongside rates.
- An LLM run is non-deterministic. Repeat runs and report the spread before comparing versions.
- Ground truth for `answerable` and `category` is defined by the generator, not by independent experts.

## Next steps

1. Run `--solver agent` on the full suite (about 40 x 2 minutes on the current endpoint), repeat several times and record the spread. Use `--limit N` for smoke tests.
2. Fix evidence rendering in the production path, then re-run to quantify the effect.
3. Build the live suite: fault injection on Online Boutique in a kind cluster, exporting scenarios in this schema with `source: "live"`, reported separately from synthetic.
4. Add independent labelling of a sample of cases, in the style of the judge agreement process in [../LLM_EVALS.md](../LLM_EVALS.md).
5. Add a CI job that runs the baselines and the determinism test on every commit (the agent run stays manual).
