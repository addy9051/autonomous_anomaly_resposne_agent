# Findings Ledger — Enterprise Air-Gapped Agent (`main`)

Status values: `OPEN` · `MITIGATED` (partial) · `FIXED` (commit + test required) · `ACCEPTED` (user/architect sign-off).
Seeded by the red-team security review of 2026-10-24 against branch `main`.

| ID | Sev | Title | Location | Status |
|---|---|---|---|---|
| ENT-001 | HIGH | In-memory hourly rate limiting for auto-remediation | `agents/action/policy.py` L49 | OPEN |
| ENT-002 | HIGH | `API_GATEWAY_SECRET` optional in non-dev environments | `api.py` L210-245 | OPEN |
| ENT-003 | MEDIUM | Ephemeral in-process fallback for episodic memory | `shared/episodic_memory.py` | OPEN |
| ENT-004 | MEDIUM | Egress check-then-connect DNS rebinding window | `shared/egress.py` | OPEN |
| ENT-005 | MEDIUM | Next.js catch-all proxy path & header forwarding | `dashboard/app/api/backend/[...path]/route.ts` | OPEN |
| ENT-006 | MEDIUM | Procedural skills lack priority caps & cryptographic signing | `shared/skills.py`, `skills/**` | OPEN |
| ENT-007 | LOW | Runtime logs and SQLite DB tracked in git on `main` | `api.out.log`, `data/incidents.db` | OPEN |
| ENT-008 | — | Proposal hash verification before action execution | `api.py`, `agents/action/policy.py` | FIXED (verified in unit tests) |
| ENT-009 | — | Telemetry simulator rejection during live verification | `agents/verification/verifier.py` | FIXED (verified in unit tests) |

---

## Detailed Findings

### ENT-001 — In-Memory Hourly Rate Limiting (HIGH)
- **Problem:** `DeterministicActionPolicy` stores execution timestamps in `cls._auto_history = defaultdict(deque)` in process memory.
- **Exploitation Impact:** If the enterprise deploys multiple ECS tasks/containers behind the internal ALB, each worker tracks limits independently ($N$ tasks = $N \times \text{limit}$). A task crash or redeploy also immediately resets the hourly quota.
- **Defensive Fix:** Migrate auto-action rate limiting to Redis (e.g., token bucket with atomic `INCR` + `EXPIRE`) or PostgreSQL table with row locks.
- **Verification Test:** Unit test spinning up two policy instances or simulating multi-worker concurrency.

### ENT-002 — `API_GATEWAY_SECRET` Optional in Non-Dev (HIGH)
- **Problem:** If `API_GATEWAY_SECRET` is not set in `.env`, `verify_auth` in `api.py` trusts the `X-Authenticated-User` header whenever `environment != "development"`.
- **Exploitation Impact:** If any internal network path (misconfigured security group, container escape, sidecar) reaches the FastAPI port directly without transiting API Gateway, an attacker can supply `X-Authenticated-User: admin` and execute/approve actions.
- **Defensive Fix:** Enforce `API_GATEWAY_SECRET` as a mandatory configuration in non-development environments (`APP_ENV != "development"`), raising a startup error if missing.

### ENT-003 — Ephemeral Episodic Memory Fallback (MEDIUM)
- **Problem:** When Postgres episodic memory fails, the agent falls back to an in-process dictionary and logs a warning.
- **Exploitation Impact:** Incidents resolved during a database glitch have their fingerprints stored only in RAM. If the container restarts, prior failures/fixes are forgotten, leading to re-triggering of suppressed duplicate incidents or re-applying failed remediations.
- **Defensive Fix:** Provide durable fallback (e.g. local persistent disk queue or fail-closed alerting) instead of silent degradation.

### ENT-004 — Egress Check-Then-Connect DNS Rebinding (MEDIUM)
- **Problem:** `check_host()` in `shared/egress.py` resolves the IP address and checks against allowed CIDRs. Then `httpx` issues the request, causing a second DNS query.
- **Exploitation Impact:** An attacker controlling a DNS server with low TTL could return an allowed IP on the first query and a forbidden private IP (`169.254.169.254` or internal microservice) on the second query.
- **Defensive Fix:** Pin the resolved IP address directly in the HTTP connection transport or utilize custom DNS resolution hooks that bind the socket to the pre-checked IP.

### ENT-005 — Dashboard Server Proxy Catch-All (MEDIUM)
- **Problem:** Next.js route `[...path]/route.ts` proxies incoming browser requests directly to FastAPI, signing them with STS credentials.
- **Exploitation Impact:** If query parameters or path segments contain encoded directory traversals (`..%2f`), requests may reach unintended backend endpoints.
- **Defensive Fix:** Explicitly allowlist proxied backend paths and sanitize path segments before constructing the downstream URL.

### ENT-006 — Skill Priority & Integrity (MEDIUM)
- **Problem:** Skills in `skills/*/SKILL.md` are loaded dynamically and routed based on frontmatter `priority`.
- **Exploitation Impact:** A developer or malicious PR could introduce a skill with `priority: 9999` that bypasses `baseline-conduct` or `critical-rca-only`.
- **Defensive Fix:** Hardcode `baseline-conduct` and `critical-rca-only` as immutable system guards that cannot be overridden by procedural skills.
