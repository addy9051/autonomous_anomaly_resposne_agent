# Positioning and Validation Plan (one page)

Status: **proposal.** Based on [02](./02-SRE_PAIN_POINTS.md), [03](./03-COMPETITIVE_LANDSCAPE.md) and [04](./04-PIVOT_OPTIONS_AND_RECOMMENDATION.md). Thresholds below are proposed targets, not validated numbers.

## Positioning

**For** SRE teams running Kubernetes in regulated or data-restricted environments, **this is** an incident investigation copilot **that** shows its work: every claim cites evidence, confidence is capped and measured, and actions are gated by code instead of by the model. **Unlike** hosted AI SRE tools, it can run fully offline on open-weight models, and it publishes a reproducible benchmark of how often it is right and how often it says "I don't know".

**Why this is defensible:** change correlation and cited evidence are commodity (see [03](./03-COMPETITIVE_LANDSCAPE.md) section 1). What no verified product combines is offline inference, a documented confidence method and a hard action gate, backed by public numbers. The numbers are the differentiator.

**Do not claim:** "first" or "unique" at change correlation, real-world accuracy from the synthetic suite, or demand for air-gapped deployment until it is validated.

## Hypotheses and kill criteria

| # | Hypothesis | How we test | Proposed pass | If it fails |
|---|-----------|-------------|---------------|-------------|
| H1 | Regulated teams cannot send incident data to hosted LLMs | 8-10 interviews with SREs in finance, health, public sector and defence | At least 5 say hosted AI is blocked or heavily restricted for incident data | Drop air-gapped as the lead message; lead on verifiability only |
| H2 | Our attribution beats simple heuristics | RCA benchmark ([06](./06-RCA_BENCHMARK.md)), agent vs `alert_service_recent_change` | Top-1 on answerable cases clearly above the best heuristic (heuristic is 0.571 on the synthetic suite) | Fix the agent or retreat to "evidence assistant", not "RCA" |
| H3 | Confidence is trustworthy | Same benchmark | ECE at or below 0.15; confident (>=0.7) errors at or below 10% | Tighten caps in `hypotheses.py` before any claim about confidence |
| H4 | The agent abstains when it should | Same benchmark | Abstains on at least 80% of unanswerable cases, and on at most 10% of answerable ones | Add abstention rules and re-run |
| H5 | SREs find the evidence checkable | Show 5-10 SREs briefs from live fault-injected runs; ask them to verify the claims | Most can verify the main claim in under 5 minutes | Rework brief format and evidence rendering |

## Sequence (about six weeks)

1. **Weeks 1-2:** run the agent through the benchmark ([06](./06-RCA_BENCHMARK.md)); fix evidence rendering (the citable evidence index shows only metric name, value and time, so commit summaries and files are not citable); extend change evidence (Phase 1 in [04](./04-PIVOT_OPTIONS_AND_RECOMMENDATION.md)).
2. **Weeks 2-4:** build live fault injection on Online Boutique (kind cluster, bad deploys, config and limit changes, dependency failure, load surge) and freeze it as the `live` suite.
3. **Weeks 1-5 in parallel:** customer interviews for H1 and H5. Use the benchmark report as the conversation piece.
4. **Week 6:** publish the benchmark report (method, suite hash, per-class results, failures) and make the go or no-go decision on the positioning.

## Decisions this plan feeds

Positioning (offline plus verifiable vs verifiable only), relationship to open-source projects (HolmesGPT, K8sGPT, Aurora), and business model. See [04](./04-PIVOT_OPTIONS_AND_RECOMMENDATION.md) section 7.
