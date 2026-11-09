# Pivot Options and Recommendation

Status: **proposal, not decided.** Inputs: [01-SKILLS_AUDIT.md](./01-SKILLS_AUDIT.md), [02-SRE_PAIN_POINTS.md](./02-SRE_PAIN_POINTS.md), [03-COMPETITIVE_LANDSCAPE.md](./03-COMPETITIVE_LANDSCAPE.md).

## 1. Starting point

The project began as a payment-reliability agent but is mostly industry-neutral. Payment-specific content is limited to a few prompts, the default `payment-gateway` service, `kafka_consumer_lag` and `fraud_score_mean` in `MetricsSnapshot`, the `throttle_merchant` and `block_issuer` actions, and merchant patterns in `shared/pii.py`. The README already calls it a "Sandboxed SRE Intelligence Layer". Moving to a general SRE investigation copilot is therefore a positioning and packaging change more than a rewrite.

Reusable strengths:
- Evidence timeline with citable ids and unavailable-source gaps.
- Hypothesis citation checks and confidence caps (`agents/diagnosis/hypotheses.py`).
- Deterministic action policy with critical incidents denied (`agents/action/policy.py`).
- Episodic memory for repeat incidents, regression detector, independent verification.
- Egress allowlist, PII scrubbing and local open-weight model support.

## 2. Options considered

Four options came from the owner-supplied research pass. Readiness was checked against the code.

| Option | Idea | Claimed readiness | Verified readiness |
|--------|------|-------------------|--------------------|
| 1. Change-aware triage and PR suspect engine | Correlate alerts with commits, diffs and rollouts. Output a draft rollback PR. | 90% | **About 40-50%.** `data_pipeline/connectors/deploy_events.py` only calls the GitHub Deployments API and records `sha` and URL. No commit or PR diffs, changed files, Kubernetes rollout events, stack-trace-to-diff mapping or PR generation. |
| 2. Open-source correlation layer (Datadog alternative) | On-demand PromQL and LogQL correlation for Prometheus and Loki teams. | 85% | **Lower.** Adapters exist for Prometheus, Datadog, Splunk and a simulator. No Loki adapter and no metric-to-log-to-deploy join. |
| 3. Living runbooks and alert-noise auditor | Auto-PRs to skills and Alertmanager rules from incidents. | 80% | **Lower.** Skills and a regression detector exist. No alert-noise analytics and no PR automation. |
| 4. Read-only SRE MCP server | Expose evidence, runbook search and memory to terminal and IDE. | 85% | **Lower.** No MCP server in the repo. PII and egress guards are reusable. |

### Security review of the options

The owner-supplied review was optimistic. Corrections:

- **Option 1:** a rollback PR lowers blast radius but needs a repo write token. Commit messages and diffs are untrusted text that can carry prompt injection. They must pass `sanitize_for_llm` and a human merge.
- **Option 2:** "zero data custody" ignores credential custody. A hosted service querying a customer's private Prometheus or Loki needs network access and credentials into their environment, which is a larger trust ask than storing logs and conflicts with an air-gapped story.
- **Option 3:** auto-generated skill edits do not defend against ENT-006 (procedural skill poisoning). They create a new path for it, because logs and commit messages are untrusted. This is safe only with mandatory human review, `CODEOWNERS` on `skills/` and immutable safety skills.
- **Option 4:** an MCP server is a new tool-injection surface, though read-only by construction.

## 3. What the research changes

1. **Change correlation is not a differentiator.** Both research passes support it as a real need, but nearly every competitor already does it (see [03](./03-COMPETITIVE_LANDSCAPE.md) section 1).
2. **The remaining gap is the combination**: air-gapped or open-weight inference, a transparent capped confidence method, and a hard deterministic action gate. Nobody was verified to offer all three. Aurora is closest.
3. **Public evidence of calibration is unclaimed.** Cleric admits raw LLM confidence is miscalibrated, and Aurora's benchmark is vendor-authored. A reproducible, open benchmark and calibration report would be new.
4. **Demand for air-gapped is unproven and probably narrow**, and platform bundling, cloud-provider agents and free OSS are real threats.

## 4. Recommendation

**Position as a verifiable, offline-capable investigation copilot for regulated Kubernetes teams.** Headline the trust properties, with change correlation as a supporting feature:

> Every claim cites evidence. Confidence is capped and measured. Actions are gated by code, not by the model. It runs fully offline.

Choice among the options: **Option 1 as the core, Option 4 as the delivery channel, a human-reviewed slice of Option 3, Option 2 deferred.**

### Phases

1. **Change context.** Add commits, PR titles, changed files and Kubernetes rollout events as citable evidence next to the GitHub deployments. Rank suspect changes. Add an explicit trigger-versus-root-cause field.
2. **Skills and de-payments.** Rewrite skills as cause-based playbooks (deploy regression, CrashLoopBackOff, OOMKilled, DNS, image pull, probe failures, dependency outage). Add the shared policy block to `baseline-conduct`. Move payment content into an optional pack and rename payment defaults. See [01](./01-SKILLS_AUDIT.md).
3. **Benchmark and calibration.** Inject bad deploys, config changes and resource limits into a public microservice demo (for example Online Boutique). Publish top-1 and top-3 suspect accuracy, correct "insufficient evidence" rate, calibration of stated confidence, and time to brief, with a harness others can rerun.
4. **Delivery.** Read-only MCP server (evidence, runbook search, episodic memory) behind PII and egress guards. Draft rollback GitOps PR, human-merged, with diffs and commit text sanitised.
5. **Learning loop, human-reviewed.** Flag repeat incidents and suggest runbook or skill edits as review items, never auto-merged.
6. **Later, on demand:** Loki adapter, alert-noise analytics, optional vertical packs.

### Out of scope for now

- Datadog-alternative positioning and a hosted multi-tenant service as the primary product.
- Competing head-on with CNCF-backed OSS on features. Decide whether to integrate with or contribute to HolmesGPT or K8sGPT.

## 5. Validation plan

Before investing beyond Phase 3:

1. **Customer discovery.** Speak with 5-10 SREs, ideally in regulated or air-gapped environments (see [../SRE_AGENT_PRODUCT_STRATEGY_BLUEPRINT.md](../SRE_AGENT_PRODUCT_STRATEGY_BLUEPRINT.md) section 11). Test whether data residency is a real buying factor. A negative result should change the plan.
2. **Benchmark results** from Phase 3 as the first proof point.
3. **Governance gates:** tests that skills only name actions in `N8N_WORKFLOWS` with correct params, no payment terms in the core, and `CODEOWNERS` plus a priority cap on `skills/` (ENT-006).

## 6. Risks and open questions

| Risk or question | Note |
|------------------|------|
| Narrow market | Most commercial vendors ship no air-gap option. Air-gapped demand is an assumption. |
| Feature convergence | Competitors closed similar gaps within about a year. |
| Free OSS overlap | Aurora, HolmesGPT, K8sGPT and kagent cover parts of the story at no cost. |
| New untrusted inputs | Commit messages, diffs and PR text enter prompts and PR drafts. |
| Readiness overstated | Phase 1 is roughly half-built, not nearly done. |
| Skills not SRE-reviewed | Outstanding per [../DECISIONS.md](../DECISIONS.md) section 5.1. |
| Business model | Open source, self-hosted licence, or services? Not yet chosen. |
| Hosted vs self-hosted | The `fully_deployed_saas/` branch is a separate, multi-tenant design with its own red-team scope. |

## 7. Decisions needed

1. Confirm the positioning (verifiable, offline-capable, regulated Kubernetes) or choose another.
2. Choose the relationship to existing OSS projects (integrate, contribute, or compete).
3. Choose the distribution and business model.
4. Approve Phase 1 and Phase 3 as the first implementation work.
