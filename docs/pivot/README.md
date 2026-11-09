# Pivot Research (November 2026)

Research and analysis behind the decision to move this project from a payment-reliability agent to a broader SRE investigation copilot. These documents are working research, not decisions. Decisions are recorded in [../DECISIONS.md](../DECISIONS.md) once made.

| Document | What it covers |
|----------|----------------|
| [01-SKILLS_AUDIT.md](./01-SKILLS_AUDIT.md) | Audit of the current `skills/` playbooks: how generic they are, what each is missing, and fixes. |
| [02-SRE_PAIN_POINTS.md](./02-SRE_PAIN_POINTS.md) | Current SRE pain points from Reddit, Stack Overflow and vendor material, with source quality noted. |
| [03-COMPETITIVE_LANDSCAPE.md](./03-COMPETITIVE_LANDSCAPE.md) | AI SRE vendors and open-source tools compared, and which capabilities are commoditised. |
| [04-PIVOT_OPTIONS_AND_RECOMMENDATION.md](./04-PIVOT_OPTIONS_AND_RECOMMENDATION.md) | Pivot options, code readiness, security review, recommendation, phases and validation plan. |
| [05-POSITIONING_AND_VALIDATION_PLAN.md](./05-POSITIONING_AND_VALIDATION_PLAN.md) | One-page positioning, hypotheses with kill criteria, and a six-week sequence. |
| [06-RCA_BENCHMARK.md](./06-RCA_BENCHMARK.md) | The benchmark harness in `evals/rca_bench/`: how to run it, metrics, suite, baseline results, limits. |
| [07-FIRST_AGENT_RUN_FINDINGS.md](./07-FIRST_AGENT_RUN_FINDINGS.md) | Results and analysis of the first full agent run, with caveats and next steps. |

Related existing docs: [SRE_AGENT_PRODUCT_STRATEGY_BLUEPRINT.md](../SRE_AGENT_PRODUCT_STRATEGY_BLUEPRINT.md), [SRE_STRATEGIC_BLUEPRINT.md](../SRE_STRATEGIC_BLUEPRINT.md), [AGENT_SKILLS_PLAYBOOKS.md](../AGENT_SKILLS_PLAYBOOKS.md), [ENTERPRISE_RED_TEAMING.md](../ENTERPRISE_RED_TEAMING.md).

## Evidence quality

Read every finding with its confidence in mind.

- **Reddit** could not be fetched directly (403 on all endpoints and mirrors). Reddit evidence is limited to thread titles and snippets returned by search, so full threads, comment counts and upvotes were not verified.
- **Stack Overflow, DevOps and Server Fault** were queried through the Stack Exchange API and worked. The SRE Stack Exchange site returned HTTP 400.
- **Vendor claims** come from vendor docs, blogs and repositories. They describe marketing and documentation, not independently tested behaviour.
- **Survey figures** (for example Splunk's State of Observability 2026) come from gated landing pages and were not independently checked. No Catchpoint or PagerDuty survey numbers are used because none could be verified.
- A second research pass supplied by the project owner contained **no source links**. Its statistics are treated as hypotheses.
