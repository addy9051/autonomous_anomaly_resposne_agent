# Enterprise Air-Gapped SRE Agent: Security Architecture & Red Teaming Guide

> **Branch:** `main`  
> **Subsystem Directory:** Root repository (`api.py`, `agents/`, `shared/`, `dashboard/`, `infra/`, `knowledge_base/`)  
> **Dedicated Skill:** [`redteam-enterprise-airgapped`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/.agents/skills/redteam-enterprise-airgapped/SKILL.md)  
> **Deployment Target:** AWS ECS Fargate + RDS PostgreSQL + Local vLLM in an Air-Gapped Private VPC

---

## 1. System Motto & Guarantees

> **"An autonomous SRE agent that is trusted to touch production because it can never act beyond deterministic policy, never talk beyond its network boundary, never trust its own inputs, and never claim a fix it cannot prove."**

Unlike a public multi-tenant SaaS that defends against unauthenticated web traffic and shared-resource exhaustion, the enterprise air-gapped agent operates in high-trust, mission-critical infrastructure where an errant remediation action can trigger production downtime.

The enterprise architecture enforces **Five Deterministic Guarantees**:

```
                                  FIVE ENTERPRISE GUARANTEES
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 1. BOUNDED AUTONOMY     │ Deterministic policy gate; Critical incidents = RCA only;    │
│                         │ Reversible low-blast allowlist; Proposal hash binding        │
├─────────────────────────┼──────────────────────────────────────────────────────────────┤
│ 2. ZERO-TRUST IDENTITY  │ ALB-OIDC → STS AssumeRole → SigV4 → API Gateway Offload      │
│                         │ Gateway sets trusted X-Authenticated-User header             │
├─────────────────────────┼──────────────────────────────────────────────────────────────┤
│ 3. AIR-GAP & EGRESS     │ Fail-closed egress allowlist in shared/egress.py             │
│                         │ Private VPC without NAT Gateway or 0.0.0.0/0 egress SG rules │
├─────────────────────────┼──────────────────────────────────────────────────────────────┤
│ 4. INPUT DISTRUST       │ PII redaction; XML prompt boundary fences; RAG score guard;  │
│                         │ Citation resolution (dropping invented IDs & capping conf)   │
├─────────────────────────┼──────────────────────────────────────────────────────────────┤
│ 5. HONEST VERIFICATION  │ Closed-loop verifier re-reads metrics after execution;       │
│                         │ Rejects simulated telemetry; Missing signals = Inconclusive  │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Dedicated Red Team Cybersecurity Expert

To enforce **Separation of Concerns (SoC)**, the enterprise system has a dedicated red-teaming expert:

- **Skill Entrypoint:** [`.agents/skills/redteam-enterprise-airgapped/SKILL.md`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/.agents/skills/redteam-enterprise-airgapped/SKILL.md)
- **Threat Model & Playbooks:** [`.agents/skills/redteam-enterprise-airgapped/references/threat_model.md`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/.agents/skills/redteam-enterprise-airgapped/references/threat_model.md)
- **Active Findings Ledger:** [`.agents/skills/redteam-enterprise-airgapped/references/findings_ledger.md`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/.agents/skills/redteam-enterprise-airgapped/references/findings_ledger.md)
- **Automated Static Audit Script:** [`.agents/skills/redteam-enterprise-airgapped/scripts/static_audit.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/.agents/skills/redteam-enterprise-airgapped/scripts/static_audit.py)

---

## 3. Trust Boundaries & Identity Path

```text
SRE Engineer Browser (OIDC Session)
         │
         ▼
Application Load Balancer (ALB)
  - Enforces corporate IdP authentication (Okta, Entra ID)
  - Injects signed JWT header with user groups
         │
         ▼
Next.js Dashboard Server (dashboard/)
  - Validates ALB JWT (signer ARN, ES256, expiration)
  - Resolves role (SRE vs PM) from user groups
  - Calls AWS STS AssumeRole (scoped SRE or PM IAM role)
  - Signs downstream requests with SigV4 via signer.ts
         │
         ▼
AWS API Gateway (IAM Authorization Offload)
  - Validates SigV4 signature and caller IAM role
  - Enforces route-level resource policy
  - Overwrites X-Authenticated-User with verified IAM identity
         │
         ▼ (Private VPC Link)
FastAPI Backend (api.py: verify_auth)
  - Resolves identity from trusted gateway header
  - Verifies optional pre-shared API_GATEWAY_SECRET
  - Passes principal to orchestrator, audit trail, and approval gates
```

---

## 4. Key Attack Vectors & Threat Playbooks

### 4.1. Bounded Autonomy & Policy Gate Bypass
- **Threat:** An adversary crafting an alert or poisoning an LLM prompt tries to trick the agent into running high-blast-radius commands (e.g. `drop_database`, `delete_namespace`, `reboot_host`).
- **Control:** 
  1. `agents/action/policy.py` never trusts the LLM's requested tier.
  2. Critical incidents are **strictly Tier 3 (RCA-only)**: no automated actions are ever generated or executed.
  3. Non-critical automated actions are constrained to a hardcoded low-risk set (`LOW_RISK_ACTIONS = {"restart_unhealthy_pod", "scale_replicas", "clear_cache", ...}`). The configurable `AUTO_ACTION_ALLOWLIST` can only **narrow** this set, never widen it.
  4. Actions require an immutable `proposal_hash` computed over canonical parameters at proposal time. Approval requests cannot alter parameters.

### 4.2. Egress Lockdown & Air-Gap Integrity
- **Threat:** Malicious dependencies, compromised runbooks, or prompt injections attempting data exfiltration to external Command & Control (C2) servers.
- **Control:**
  1. Network level: Private VPC subnets with no Internet Gateway and no NAT Gateway.
  2. Application level: `shared/egress.py` installs a fail-closed request hook on `httpx.AsyncClient` that validates all outbound destinations against `EGRESS_ALLOWED_HOSTS` and `EGRESS_ALLOWED_CIDRS`.
  3. Startup self-test (`egress_self_test()`) verifies that public IPs fail closed.

### 4.3. Prompt & Procedural Memory Poisoning
- **Threat:** Malicious runbook markdown or forged telemetry logs containing prompt injection sequences.
- **Control:**
  1. PII sanitization in `shared/pii.py` redacts credentials and tokens before prompt synthesis.
  2. RAG score guard (`knowledge_base/score_guard.py`) scores ingested runbooks for adversarial injection markers and filters suspicious entries.
  3. `agents/diagnosis/hypotheses.py` resolves citations against authoritative telemetry. If citations are missing or invented, confidence is capped at $\le 0.40$.

### 4.4. Verification Deception
- **Threat:** A flawed remediation is executed, and simulated or cached metrics fool the agent into claiming the service has recovered.
- **Control:**
  1. `agents/verification/verifier.py` strictly filters out simulator-sourced records (`connector_source == "simulator"` or `is_simulated == True`) when `dry_run=False`.
  2. Requires all critical health signals (`error_rate`, `latency_p99`) to be present and fresh. If signals are missing, the outcome fails safe to `INCONCLUSIVE`.

---

## 5. Active Findings Ledger (`ENT-001` to `ENT-007`)

| ID | Severity | Title | Vulnerability & Attack Surface | Recommended Defense |
|---|---|---|---|---|
| **ENT-001** | `HIGH` | In-memory hourly rate limiting | `policy.py` stores execution timestamps in process memory (`cls._auto_history`). Multiple ECS replicas track limits independently ($N \times \text{limit}$), and container restarts wipe history. | Migrate rate limiting to Redis token bucket or PostgreSQL table with row-level locks. |
| **ENT-002** | `HIGH` | `API_GATEWAY_SECRET` optional in non-dev | If `API_GATEWAY_SECRET` is unset, `verify_auth` trusts `X-Authenticated-User` unconditionally when `require_gateway_auth=True`. Any internal bypass path allows identity spoofing. | Require `API_GATEWAY_SECRET` as a mandatory non-dev environment variable; fail closed on startup if missing. |
| **ENT-003** | `MEDIUM` | Ephemeral episodic memory fallback | If PostgreSQL is unreachable, episodic memory degrades to an in-memory dictionary. Memory of failed fixes is lost on restart. | Provide durable local SQLite queue or fail-closed alerting rather than silent in-memory degradation. |
| **ENT-004** | `MEDIUM` | Check-then-connect DNS rebinding window | `shared/egress.py` checks IP via `socket.getaddrinfo`, then `httpx` issues a second DNS query at connect time. | Pin the resolved IP address directly to the HTTP socket transport. |
| **ENT-005** | `MEDIUM` | Next.js dashboard catch-all proxy path forwarding | `[...path]/route.ts` proxies arbitrary paths to the backend. Path traversal sequences (`..%2f`) could target unintended endpoints. | Implement an explicit allowlist of forwardable backend API endpoints. |
| **ENT-006** | `MEDIUM` | Procedural skills lack priority caps & signing | Skills in `skills/*/SKILL.md` are loaded dynamically by priority. A rogue skill could attempt to supersede `baseline-conduct`. | Make safety skills (`baseline-conduct`, `critical-rca-only`) immutable system guards; add GitHub `CODEOWNERS` on `skills/`. |
| **ENT-007** | `LOW` | Runtime logs and SQLite DB tracked in git | `api.out.log`, `data/incidents.db` are tracked on `main`. | Remove from git tracking and ensure strictly gitignored. |

---

## 6. How to Run the Enterprise Security Audit & Tests

### Static Security Audit
The audit script scans Python routes, policy engines, and Terraform configs:

```powershell
# Audit working tree
.venv\Scripts\python.exe .agents/skills/redteam-enterprise-airgapped/scripts/static_audit.py

# Audit git ref 'main'
.venv\Scripts\python.exe .agents/skills/redteam-enterprise-airgapped/scripts/static_audit.py --ref main

# Scan commit history for credentials
.venv\Scripts\python.exe .agents/skills/redteam-enterprise-airgapped/scripts/static_audit.py --history
```

### Security Regression Unit Tests
The enterprise codebase contains automated security unit tests:

```powershell
poetry run pytest `
  tests/unit/test_auth_security.py `
  tests/unit/test_get_endpoint_auth.py `
  tests/unit/test_action_tiers.py `
  tests/unit/test_agent_controls.py `
  tests/unit/test_search_safety.py `
  tests/unit/test_ingest_guard.py `
  tests/unit/test_pii.py `
  tests/unit/test_tracing_lockdown.py `
  tests/unit/test_sandboxed_layer.py `
  tests/unit/test_verifier.py -v
```
