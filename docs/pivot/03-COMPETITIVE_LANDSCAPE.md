# Competitive Landscape: AI SRE Tools (November 2026)

Source: vendor docs, blogs, pricing pages and GitHub repositories fetched during research. Items marked "unconfirmed" could not be resolved from direct sources. This is documentation-level evidence, not independent testing. See [Evidence quality](./README.md#evidence-quality).

## Comparison

| Product | Change/deploy correlation | Evidence-cited, confidence method | Self-host / air-gap / BYO model | Remediation stance and gate | K8s / MCP / licence |
|---|---|---|---|---|---|
| Datadog Bits Investigation | Yes (git-tagged APM, K8s events) | Citations yes. No numeric confidence (validated / invalidated / inconclusive). | No. SaaS only, excluded from GovCloud. | Recommend, then "Bits Guardrails" admin approval. One-click execution is in Preview. | Multi-signal. MCP yes. Proprietary, metered AI credits. |
| PagerDuty "Paige" | Yes (Recent Changes, probable origin %) | Marketing shows a confidence figure from a mockup, no documented algorithm. | Not found. | Approval-gated, can run end to end on sign-off. No guardrail doc page found. | Agnostic. MCP yes (remote). Proprietary. |
| incident.io Investigations | Yes (deploys, flags, PR diffs) | Most rigorous found: "conviction" tiers and a post-hoc grading rubric. | No (OpenAI, Anthropic, Vertex only). | Most conservative: PRs only, human-merged. | Not K8s-specific. MCP hub yes. Proprietary, add-on price not public. |
| Rootly AI SRE | Yes, commit level | Code decides outcome and caps confidence when there is no evidence. | Partial: bring-your-own LLM key, and a read-only "Private Agent". Not on-prem inference. | Read-only infrastructure agent, PRs only. | Deep K8s via Private Agent. MCP yes (about 280 tools). Proprietary. |
| Resolve.ai | Yes (causal timelines, suspect commits) | Citations yes. No numeric confidence. | "Satellite" proxies on-prem data in, but reasoning runs in the cloud. No BYO model. | Strict human approval, separate propose and execute engines. | K8s and ECS. MCP both ways. Proprietary. |
| Cleric | Yes | Citations yes. Says raw LLM confidence is miscalibrated and uses an outcome verifier. | No (single-tenant cloud). | Read-only, then approved writes. | Agnostic. MCP yes. Proprietary, public pricing ($700-3000/month). |
| Traversal | Yes, including pre-merge PR risk | Citations yes. Confidence shown as case-study percentages. | BYOC: runs in the customer's AWS account with in-VPC Bedrock. Not air-gapped. | Read-only by default, opt-in self-healing. | Not K8s-only. MCP yes. Proprietary. |
| Komodor (KAOP) | Yes (PR review, rollout correlation) | Evidence and audit trail yes. No numeric confidence. | Strongest proprietary story: Helm / on-prem, bring any model. | Most formal gate: five-layer deterministic gate before any action. | K8s-rooted. MCP gateway yes. Proprietary. |
| Grafana Sift / Assistant | Partial (K8s config changes, no git) | Best citation UI. No confidence score. | Hybrid (backend in Cloud). OSS LLM plugin supports Ollama and vLLM. | Read-only, no remediation execution. | Many sources. MCP yes. OSS plugin plus proprietary cloud. |
| Dynatrace Intelligence | Yes (named commit) | "Deterministic, causation-based". Explicitly rejects probability scores. | Dynatrace Managed (on-prem) active. AI-suite coverage on Managed unconfirmed. | Full range with allow-list and approval guardrails. | High K8s. MCP yes. Proprietary. |
| New Relic SRE Agent | Deployment level only | "Ground Truth" language, no citation UI or score. | Not found. | Approval-gated, generic governance. | Low-moderate K8s. MCP yes. Proprietary. |
| Splunk AI | Not found | "Transparent reasoning", no score. | Splunk Enterprise core is air-gap capable. AI features unconfirmed. | Recommend-only, human oversight. | Low K8s. MCP yes. Proprietary. |
| Harness AI SRE | Deploy and flag level | Confidence figure shown only in a UI mockup, no methodology. | Not found. | Progressive trust automation, no documented gate. | No K8s focus. Proprietary. |
| HolmesGPT (CNCF, OSS) | Yes via GitHub-MCP / Robusta toolset | Tool-call evidence inline. No numeric confidence. | Yes: Ollama (experimental), LiteLLM, OpenAI-compatible proxy. | Read-only core with a hard-coded auto-approve vs human-approve split. | Infra-agnostic. MCP client, not server. Apache-2.0. |
| K8sGPT + operator (CNCF, OSS) | No (point-in-time scan) | None. | Yes: ollama, localai, litellm backends. | Best deterministic remediation gate found: re-fetch object, single allow-listed JSON patch path, dry run, resourceVersion check. | K8s only. Is an MCP server. Apache-2.0. |
| kagent (CNCF, OSS) | No (framework) | No. | Yes (Ollama). | No built-in gate. | K8s-native. MCP server and client. Apache-2.0. |
| Aurora (Arvo-AI, OSS) | Yes | Structured RCA and postmortem, no calibrated score. | Yes: explicit air-gapped bundle, fully self-hosted, no telemetry. | Sandboxed investigation, PR-gated fixes, SigmaHQ and NeMo guardrails. | Multi-cloud and K8s. MCP yes. Apache-2.0, vendor-authored benchmark only. |
| AWS DevOps Agent | Yes | "Deterministic proofs" for permission checks. No RCA confidence score. | No (managed). | Documentation ambiguous between auto-remediate and recommend-only. | Not K8s-specific. Exposes MCP / ACP / A2A. Proprietary. |
| Azure SRE Agent | Yes (named-commit example) | Source-cited answers, no score. | Not found. | Review (default) or Autonomous, plus deterministic "Command" hooks. | Infra-agnostic. Proprietary. |
| Google Gemini Cloud Assist Investigations | Not found | Source-linked observations, multiple hypotheses when uncertain. | No (managed). | Strictly read-only. | GKE among many. Proprietary. |

Uncertain items: Komodor's "Klaudia" naming and history, Harness's actual gating mechanism, AWS DevOps Agent's contradictory autonomy docs, and Splunk's AI-feature air-gap support.

## 1. Commoditised vs rare

**Commoditised (many vendors):**
- Telemetry-level change and deploy correlation.
- Evidence-referencing ("grounded") output.
- Human-approved remediation as the default stance.
- MCP support (server or client).
- Free open-source tools that already run against local models.

**Rare (zero to three vendors):**
- A documented capped or calibrated confidence method: only incident.io and Rootly describe the capping logic. Dynatrace rejects probability scores, and PagerDuty and Harness show numbers only in mockups.
- Genuinely self-hosted or air-gapped inference: Komodor, Traversal (BYOC, not air-gapped), Dynatrace Managed (AI coverage unconfirmed) and the OSS tools.
- A formal deterministic action gate independent of LLM judgment: Komodor, Azure Command hooks, K8sGPT's operator and Resolve's separated engines.

## 2. Closest to this project's combination

No product was verified to offer air-gapped inference, a transparent capped-confidence method and a hard deterministic action gate together.

- **Aurora** is closest: air-gapped bundle, MCP, PR-gated fixes. It lacks calibrated confidence and its only benchmark is vendor-authored.
- **K8sGPT** has the strongest deterministic gate and local-LLM backends, but no change correlation or citation depth and is K8s-only.
- **HolmesGPT** has a clean read-only and write split with Ollama support, no confidence scoring, and is not itself an MCP server.
- **Komodor** pairs self-host and bring-your-own model with the strongest proprietary gate, but does not claim air-gap and has no confidence score.

## 3. Demand for self-hosted or air-gapped AI SRE

- **For:** vendors keep building BYOC and on-prem options (Traversal, Komodor, Resolve's Satellite), and Splunk Enterprise and Dynatrace Managed remain on offer, which implies a paying regulated, defence and finance segment. GitHub stars for local-capable tools (K8sGPT 8.3k, kagent 4.2k, HolmesGPT 3.8k) are only a weak proxy.
- **Against:** the commercially successful vendors (Datadog, PagerDuty, incident.io, Cleric, New Relic, Harness) ship no air-gap option and grow as hosted SaaS, suggesting most buyers accept hosted LLMs.
- **No independent survey or forum evidence** of demand was found. This remains the largest open assumption.

## 4. Threats to a small new entrant

1. **Bundling:** Datadog, Dynatrace, PagerDuty and New Relic add AI SRE as upsells, compressing standalone pricing.
2. **Cloud-provider agents:** AWS, Azure and Google offer deeply integrated first-party agents.
3. **Mature free OSS:** HolmesGPT, K8sGPT, kagent and Aurora already cover local LLMs and some deterministic gating.
4. **Consolidation** toward fewer, larger owners (for example the apparent absorption of AppDynamics into Splunk and Cisco).
5. **Fast feature convergence:** nearly every vendor added citations, change correlation and MCP within about a year, so a gap can close quickly once demonstrated.

## Implication

Change-aware, evidence-cited, recommend-only Kubernetes RCA describes the current market and is not a gap. Any edge has to come from the combination in section 2, backed by public, reproducible evidence. See [04-PIVOT_OPTIONS_AND_RECOMMENDATION.md](./04-PIVOT_OPTIONS_AND_RECOMMENDATION.md).
