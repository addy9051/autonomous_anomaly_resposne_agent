# Findings Ledger — Multi-Tenant SaaS

Status values: `OPEN` · `MITIGATED` (partial) · `FIXED` (commit + proof required) · `ACCEPTED` (user sign-off).
Seeded by the red-team pass of 2026-10-24 against `20a95f9`, using code reading only (nothing was exploited live).

| ID | Sev | Title | Location | Status |
|---|---|---|---|---|
| SAAS-001 | CRITICAL | Tenant takeover via registration upsert | `api/routes/auth_routes.py` L40-59 | OPEN |
| SAAS-002 | CRITICAL | Live tenant key burned (git history + public page) | commit `21e6e96` | OPEN |
| SAAS-003 | HIGH | Neon DB password printed by E2E test | `test_end_to_end_neon.py` L35 | OPEN |
| SAAS-004 | HIGH | Stored XSS → API-key theft from localStorage | `dashboard/index.html` L460-505 | OPEN |
| SAAS-005 | HIGH | Approval: no hash re-verify, non-atomic, no SoD | `api/routes/incident_routes.py` L132-226 | OPEN |
| SAAS-006 | MEDIUM | LLM `action_type` not validated against closed catalog | `agents/diagnosis.py` L141, `agents/policy.py` | OPEN |
| SAAS-007 | MEDIUM | SSRF guard checks IP literals only (DNS/redirect bypass) | `connectors/adapters.py` | MITIGATED |
| SAAS-008 | MEDIUM | Denial-of-wallet: open registration + sync LLM per alert | `auth_routes.py`, `alert_routes.py` | OPEN |
| SAAS-009 | MEDIUM | User-controlled `plan` at registration | `auth_routes.py` L24 | OPEN |
| SAAS-010 | MEDIUM | No RLS; UPDATEs filter by `incident_id` only; unbounded `limit` | `incident_routes.py`, `schema.sql` | OPEN |
| SAAS-011 | LOW | `/health` leaks env+version; `/docs` public | `api/main.py` | OPEN |
| SAAS-012 | — | CORS wildcard + credentials | `api/main.py` | FIXED `20a95f9` |
| SAAS-013 | — | Prompt data not fenced | `agents/diagnosis.py` | MITIGATED `20a95f9` (fence not escaped) |

## Details

### SAAS-001 — Tenant takeover via registration upsert (CRITICAL)
`INSERT INTO tenants ... ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name RETURNING id`
then mints a new key for that `tenant_id`. `tenant_id` is derived deterministically from
the org name, so anyone can `POST /api/v1/auth/register {"organization_name":"Production Live Team"}`
and receive a valid admin key for `tenant-production-live-team`.
**Fix:** `ON CONFLICT DO NOTHING` → 409 if no row returned; add random suffix to tenant id;
require an invite/admin secret or email verification; rate limit.
**Proof:** unit test that a second registration with the same name returns 409 and creates no key.

### SAAS-002 — Burned tenant key (CRITICAL)
`sre_live_OPNF…` was committed in `21e6e96`, pushed to GitHub and served from `/dashboard`.
Removing it from HEAD (`20a95f9`) does not un-leak it.
**Fix:** `UPDATE tenant_api_keys SET is_revoked = TRUE WHERE key_prefix = 'sre_live_OPNF';`
then mint a new key via `POST /api/v1/auth/keys` using a fresh key. Optionally purge history.

### SAAS-003 — DSN password in logs (HIGH)
`print(settings.database_url[:45])` emits `postgresql://neondb_owner:npg_…@ep…` — the
password segment is inside the first 45 chars. It appeared in agent task logs.
**Fix:** rotate the Neon role password; print only `urlparse(dsn).hostname`.

### SAAS-004 — Stored XSS (HIGH)
`renderIncidentList()` / `loadIncidentDetail()` interpolate `inc.title`, `inc.service`,
`inc.severity`, `data.root_cause`, `action_type`, `err.message` into `innerHTML`. Anyone with
an ingestion key (or a poisoned upstream alert source) can run JS in an SRE's browser and
read `localStorage.sre_api_key`.
**Fix:** escape helper / `textContent`, remove inline `onclick` with interpolated ids, add CSP
`default-src 'self'; script-src 'self'` (move inline JS to a file), consider `sessionStorage`.

### SAAS-005 — Approval gate weaknesses (HIGH)
(a) Docstring says "verifies proposal hash" but the hash is never recomputed/compared.
(b) Select-then-update is not atomic → double execution under concurrency.
(c) The key that ingested the alert can approve its own remediation (no separation of duties;
every key is `role="sre"`). (d) `UPDATE incidents ... WHERE incident_id = $1` lacks tenant filter.
**Fix:** `UPDATE action_proposals SET status='executing' WHERE id=$1 AND tenant_id=$2 AND status='pending' RETURNING *`;
recompute `compute_action_proposal_hash` and compare with `hmac.compare_digest`; key scopes.

### SAAS-006 — Open action catalog (MEDIUM)
LLM-provided `action_type`/`parameters` are stored and rendered as an approvable action
without enum/bounds validation. **Fix:** closed `Literal[...]` catalog + per-action parameter schema with bounds.

### SAAS-007 — SSRF guard gaps (MEDIUM, mitigated)
`is_safe_external_url` blocks IP literals and a few hostnames but does not resolve DNS,
so `127.0.0.1.nip.io`, rebinding, and httpx redirects bypass it. **Fix:** resolve all
addresses, require `ip.is_global`, `follow_redirects=False`, validate at connector save time too.

### SAAS-008 — Denial-of-wallet (MEDIUM)
Unauthenticated, unthrottled registration (each seeds runbooks + embeddings) and synchronous
LLM per webhook let one actor exhaust OpenRouter free quota / Render RAM for every tenant.
**Fix:** per-IP + per-tenant rate limits (e.g. `slowapi`), per-tenant daily LLM budget,
fingerprint dedupe before LLM, request body size cap.

### SAAS-009 — Plan self-upgrade (MEDIUM)
`plan` is accepted from the client. **Fix:** ignore input; server sets `starter`.

### SAAS-010 — Missing defense-in-depth (MEDIUM)
Tenant isolation is app-layer only. **Fix:** RLS policies keyed on `current_setting('app.tenant_id')`,
set per acquired connection; cap `limit` (`Query(50, le=200)`); tenant filter on all UPDATEs.

### SAAS-011 — Info disclosure (LOW)
`/health` returns `environment` and `version`; `/docs` and `/openapi.json` enumerate the API.
**Fix:** minimal health body; `docs_url=None` when `APP_ENV=production`.
