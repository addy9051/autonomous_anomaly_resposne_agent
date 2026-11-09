# SRE Pain Points (late 2026)

Two research passes. Pass A (assistant-run) used Reddit search snippets, the Stack Exchange API and vendor material. Pass B was supplied by the project owner and has no source links. See [Evidence quality](./README.md#evidence-quality) before relying on any item.

## Ranked pain points (Pass A, with links)

| # | Pain point | Signal | Example sources |
|---|-----------|--------|-----------------|
| 1 | Alert fatigue and noisy, unactionable alerts | Very high, recurring across years | r/sre [Alert fatigue is killing me](https://www.reddit.com/r/sre/comments/1nm7sbi/alert_fatigue_is_killing_me/), [Tips for dealing with alert fatigue](https://www.reddit.com/r/sre/comments/1buyr5j/tips_for_dealing_with_alert_fatigue/) |
| 2 | On-call burnout | Very high | r/sre [How do you not burn out from on-call?](https://www.reddit.com/r/sre/comments/1so73te/how_do_you_not_burn_out_from_oncall/) |
| 3 | Tool sprawl and fragmented incident context | High | r/sre [observability costs thread](https://www.reddit.com/r/sre/comments/1ow3ltg/our_observability_costs_are_now_higher_than_our/): three query languages across Datadog, Splunk and Sentry |
| 4 | Observability cost and cardinality | High | Same thread: about $97k/month on tooling against about $52k of infrastructure |
| 5 | Stale runbooks and tribal-knowledge loss | High | r/devops [senior SRE left](https://www.reddit.com/r/devops/comments/1o7p2bq/): 45-minute P1 delay from a 2-year-old document |
| 6 | Postmortems without follow-through, repeat incidents | High | r/devops [same root cause as June](https://www.reddit.com/r/devops/comments/1oeyqqd/): connection-pool fix sat in the backlog for 4 months |
| 7 | Slow RCA, "trigger" confused with "root cause" | Medium-high | r/sre [AI RCA tools thread](https://www.reddit.com/r/sre/comments/1k8x5mc/): a Rootly team member concedes the conflation |
| 8 | Kubernetes debugging (CrashLoopBackOff, OOMKilled, DNS) | Very high volume | Stack Overflow [CrashLoopBackOff, 457k views](https://stackoverflow.com/questions/41604499), [how to debug it, 100k+ views](https://stackoverflow.com/questions/44673957) |
| 9 | Distrust of AI accuracy | High | incident.io [Building Investigations](https://incident.io/blog/building-investigations-what-it-takes-to-build-an-ai-sre): two years before they trusted their own AI RCA |
| 10 | Governance and cost of AI agents | Medium (vendor survey) | Splunk State of Observability 2026 (2,050 respondents): 83% worry about unintended consequences of AI agents, 55% cannot control AI costs. Gated page, not independently checked. |

## What SREs say about AI SRE tools

- **Want:** faster investigation (timelines, summaries, likely culprit) with a human still deciding, and tools that read their code and logs.
- **Distrust:** hallucination, confusing trigger with cause, and not being able to tell whether an answer is right.
- **Unmet demand:** an r/sre poster ([Agentic help to reduce alert fatigue](https://www.reddit.com/r/sre/comments/1udhck8/agentic_help_to_reduce_alert_fatigue/)) called Datadog's built-in agent "basically hot garbage" and found RunWhen better but a black box on root cause because it cannot see source code. They were considering building their own.
- **Market direction:** PagerDuty's [SRE Agent Benchmark](https://www.pagerduty.com/resources/ai/learn/sre-agent-benchmark-report/) scores agents on evidence grounding and explanation quality. PagerDuty also markets a "deterministic gap" and ships workflow actions to bridge recommendations and safe execution.

## Pass B themes (unsourced, treat as hypotheses)

| Theme | Pass B claim | Status |
|-------|--------------|--------|
| Context-gathering tax | Responders spend 15-25 minutes opening 6-8 tools before acting | Plausible, consistent with pain points 3 and 5. The figure is unsourced. |
| Skepticism of autonomous remediation | Blast-radius fear, "glorified ChatGPT wrapper", and loss of mental models ("Children of the Magenta") | Consistent with pain point 9 and the Splunk survey. Wording unsourced. |
| Datadog cost vs open-source correlation toil | Teams leaving Datadog hit correlation toil joining PromQL, git SHAs, pod events and Loki logs | Consistent with pain points 3 and 4. Stack Overflow evidence not linked. |
| Change-driven incidents | "70-80% of incidents trace to a change" | Commonly cited from Google's SRE book, not verified here. More PRs from AI coding assistants may raise the burden (unsourced). |
| Runbook rot and postmortem theater | Confluence or Notion runbooks rot within weeks | Consistent with pain points 5 and 6. |
| Alert noise | "Up to 95% of pages require no action" | Unsourced. Treat as unverified. |

## Where the two passes agree

Alert fatigue, runbook rot and repeat incidents, distrust of AI that does not cite evidence, tool sprawl and cost. Change correlation appeared in both: Pass B named it directly and Pass A found the r/sre request for RCA that can see source code. Agreement between independent searches is the strongest evidence in this set, but both lack full thread verification.

## What the pain points do and do not imply

- **An investigation agent helps with:** context assembly, slow RCA, trust, repeat incidents.
- **It does not address:** alert fatigue, burnout, observability cost and postmortem follow-through. Those are upstream of an agent that starts after an alert fires, and are owned by alerting and on-call products.
- **Demand for air-gapped deployment was not confirmed** by any thread found. See [03-COMPETITIVE_LANDSCAPE.md](./03-COMPETITIVE_LANDSCAPE.md) section 3.

## Gaps in this research

- Reddit threads were not opened in full, so sentiment strength and counts are unverified.
- Privacy sentiment about sending telemetry to hosted LLMs and sentiment about letting AI remediate production were not covered (search rate limit).
- No named-competitor user reviews were collected.
- No Catchpoint SRE Report or PagerDuty State of Digital Operations figures could be verified.
