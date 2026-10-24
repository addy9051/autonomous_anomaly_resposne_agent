---
name: redteam-saas-multitenant
description: >-
  Offensive security expert for the PUBLIC, MULTI-TENANT, $0-idle-cost SRE SaaS in
  `fully_deployed_saas/` (branch `feature/neon-saas-production`, live on Render + Neon +
  OpenRouter free tier + local FastEmbed). Use this skill whenever code, schema, config,
  dashboard, Dockerfile, render.yaml or env vars under `fully_deployed_saas/` change, before
  any deploy/push of that branch, or when the user asks for a security review, threat model,
  pentest or red-team of the SaaS. Do NOT use for the air-gapped enterprise agent on `main`
  (use `redteam-enterprise-airgapped` instead).
---

# Red Team: Multi-Tenant SaaS (`fully_deployed_saas`)

## Product motto (what we are protecting)
> "Any SRE team on the internet can self-register and get a governed, autonomous incident
> copilot — with zero fixed infrastructure cost and zero cross-tenant leakage."

Every finding is ranked by how badly it breaks one of these four promises:

| Promise | Crown jewel | Worst-case breach |
|---|---|---|
| **Tenant isolation** | `tenant_id` boundary on every row, key, vector, audit log | Tenant A reads/acts on tenant B |
| **Governed autonomy** | Policy gate + approval + `proposal_hash` | Attacker-driven remediation executes |
| **Secret custody** | `DATABASE_URL`, `ENCRYPTION_MASTER_KEY`, `OPENROUTER_API_KEY`, tenant API keys, connector creds | Full DB / all-tenant compromise |
| **$0 cost survivability** | Free-tier quotas (OpenRouter, Neon CU-hours, Render 512 MB) | One tenant exhausts shared quota → outage for all (denial-of-wallet) |

Attacker personas to emulate: **anonymous internet user**, **malicious registered tenant**,
**holder of a leaked ingestion key**, **poisoned telemetry / alert source**, **malicious
runbook author**. Full model: [threat_model.md](./references/threat_model.md).

## Workflow (run in this order, every time)

1. **Scope the diff.** `git diff --name-only HEAD~1 -- fully_deployed_saas/` (or the
   staged diff). Map each changed file to the attack surfaces in
   [threat_model.md](./references/threat_model.md#attack-surface-map).
2. **Run the static audit** (fast, read-only, no network):
   `.venv\Scripts\python.exe .agents/skills/redteam-saas-multitenant/scripts/static_audit.py`
   Add `--ref <git-ref>` to audit another commit. Every `[CRITICAL]`/`[HIGH]` hit must be
   triaged (true positive → ledger; false positive → note why).
3. **Walk the attack playbooks** in the threat model for each touched surface. Reason as the
   attacker: write the concrete request/payload that would exploit it.
4. **Check the ledger.** Re-verify every `OPEN` item in
   [findings_ledger.md](./references/findings_ledger.md) that the diff touches. Never mark a
   finding `FIXED` without pointing at the commit + the test/probe that proves it.
5. **Prove, don't assert.** Prefer a failing-then-passing unit test or a local probe against
   `uvicorn api.main:app` with `APP_ENV=development`. Live probes against
   `https://sre-saas-api.onrender.com` are allowed **only** with the user's own test tenant,
   must be read-only or self-contained, and must never touch another tenant's data.
6. **Report** with the template below, then append/update the ledger.

## Hard rules
- Never print, log, commit or echo secrets (DSNs, keys, tokens) — including partial
  prefixes that contain the password segment of a DSN. Mask as `postgresql://user:***@host`.
- A key that ever reached git history, a public page, a log, or an agent transcript is
  **burned**: the fix is revoke + rotate, not "remove from HEAD".
- Never weaken a control to make a test pass. Never disable auth "for the demo".
- Do not run destructive SQL on Neon `production`; use a Neon branch for experiments.

## Report template
```
### 🛡️ Red Team Review — SaaS (<commit or diff scope>)
| ID | Sev | Promise broken | Surface | Exploit (1 line) | Fix | Status |
Exploit narrative for every CRITICAL/HIGH (request → effect).
Residual risk + what was NOT tested.
```
Severity: CRITICAL = cross-tenant access, unauthenticated action/takeover, master-secret
exposure. HIGH = authenticated privilege escalation, XSS→key theft, SSRF, approval bypass.
MEDIUM = DoS/denial-of-wallet, info leak, missing defense-in-depth. LOW = hygiene.
