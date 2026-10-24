---
name: redteam-enterprise-airgapped
description: >-
  Offensive security expert for the ENTERPRISE, AIR-GAPPED autonomous anomaly-response agent
  on the `main` branch (LangGraph multi-agent diagnosis, deterministic action policy, egress
  allowlist, local vLLM, AWS API Gateway SigV4 + ALB-OIDC Next.js dashboard, Terraform VPC).
  Use this skill whenever files outside `fully_deployed_saas/` change on `main` — `api.py`,
  `agents/**`, `shared/**`, `knowledge_base/**`, `skills/**`, `dashboard/**`, `infra/**`,
  `evals/**`, CI configs — before merging to `main`, or when the user asks for a security review,
  threat model, pentest or red-team of the enterprise agent. Do NOT use for the public
  multi-tenant SaaS (use `redteam-saas-multitenant` instead).
---

# Red Team: Enterprise Air-Gapped Agent (`main`)

## Product motto (what we are protecting)
> "An autonomous SRE agent that is trusted to touch production because it can never act beyond
> deterministic policy, never talk beyond its network boundary, never trust its own inputs, and
> never claim a fix it cannot prove."

Every finding is ranked by which of these five guarantees it breaks:

| Guarantee | Enforced by | Worst-case breach |
|---|---|---|
| **Bounded autonomy** | `agents/action/policy.py`, `tiers.py`, approval + proposal hash, kill switch (`/agent/controls`) | Agent or attacker executes an unapproved / high-blast-radius action |
| **Zero-trust identity** | ALB-OIDC → `dashboard/lib/server/auth.ts` → STS AssumeRole → SigV4 → API GW → `X-Authenticated-User` → `verify_auth` | Forged identity approves actions; audit attributes to wrong human |
| **Air-gap / egress** | `shared/egress.py` + VPC (no NAT, no 0.0.0.0/0) | Data exfiltration, C2, model/RAG supply-chain pull |
| **Input distrust** | PII sanitizer, prompt fences, RAG `score_guard`, synthetic provenance, citation capping | Poisoned telemetry/runbook/skill/memory steers diagnosis → action |
| **Honest verification** | `agents/verification/verifier.py` (authoritative, non-simulated, fresh evidence) | Fake "recovered" closes incident; regression hidden |

Attacker personas: **insider with repo write**, **compromised telemetry source / log line**,
**malicious runbook or skill author**, **network-adjacent caller in the VPC**, **compromised
dashboard session**, **supply-chain (model mirror, pip/npm, container tag)**, **the LLM itself
(misaligned or jailbroken output)**. Full model: [threat_model.md](./references/threat_model.md).

## Workflow (run in this order, every time)

1. **Scope the diff** against `main`: `git diff --name-only main...HEAD` (or the staged diff).
   Map files to surfaces in [threat_model.md](./references/threat_model.md#attack-surface-map).
   Audit `main` without checking it out by passing `--ref main` to the script.
2. **Run the static audit** (read-only, no network):
   `.venv\Scripts\python.exe .agents/skills/redteam-enterprise-airgapped/scripts/static_audit.py --ref main`
3. **Walk the playbooks** for each touched surface; write the concrete exploit input.
4. **Run the existing security test suites** — they are the regression net for this project:
   `poetry run pytest tests/unit/test_auth_security.py tests/unit/test_get_endpoint_auth.py tests/unit/test_action_tiers.py tests/unit/test_agent_controls.py tests/unit/test_search_safety.py tests/unit/test_ingest_guard.py tests/unit/test_pii.py tests/unit/test_tracing_lockdown.py tests/unit/test_sandboxed_layer.py tests/unit/test_verifier.py -q`
   and the adversarial evals (`evals/poison_holdout.py`, `evals/guard_benchmark.py`) when RAG,
   guards or prompts change. A new control without a new test is not done.
5. **Check the ledger** [findings_ledger.md](./references/findings_ledger.md); re-verify items the diff touches.
6. **Report** (template below) and update the ledger.

## Hard rules
- Never enable live execution (`dry_run=False`, `AUTO_REMEDIATION_ENABLED=true`) to "test" an exploit.
- Never add hosts to `EGRESS_ALLOWED_HOSTS` / CIDRs to Terraform to make something work without flagging it as a boundary change.
- Treat edits to `skills/**/SKILL.md`, `knowledge_base/**`, and episodic memory as **prompt-level code**: review them like executable code.
- Terraform/IAM changes: any `*` action/resource, `0.0.0.0/0`, public subnet, or mutable image tag is at least HIGH until justified.

## Report template
```
### 🛡️ Red Team Review — Enterprise (<commit/diff>)
| ID | Sev | Guarantee broken | Surface | Exploit (1 line) | Fix | Test that proves it | Status |
Exploit narrative for every CRITICAL/HIGH. Residual risk + what was NOT tested.
```
Severity: CRITICAL = unapproved real action, identity forgery, egress escape. HIGH = policy/
verifier bypass, memory/RAG/skill poisoning that changes actions, IAM over-grant. MEDIUM =
defense-in-depth gaps, DoS, PII leak to traces. LOW = hygiene.
