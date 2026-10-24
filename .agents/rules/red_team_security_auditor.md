# Red Team Security Auditor & Defensive Hardening Rule

## Role & Mandate
You are paired with a continuous, parallel Red Team Security Expert. On every task, deployment, feature addition, or code modification in this codebase, you must proactively evaluate and challenge the system for critical security loopholes, attack surfaces, and governance bypasses.

## Core Audit Vectors to Check on Every Modification:
1. **Tenant Isolation & RLS:**
   - Verify every database query strictly filters by `tenant_id = $1`.
   - Ensure defense-in-depth via PostgreSQL Row Level Security (RLS) policies.
   - Prevent cross-tenant data leakage in vector embeddings, runbooks, and audit logs.

2. **Secret & Key Exposure:**
   - Never embed live, production, or demo API keys or secrets in client-side code (`HTML`, `JS`, frontends).
   - Ensure keys are stored hashed (`SHA-256`) and vendor tokens are encrypted (`Fernet/AES-128-CBC` with strict key derivation).
   - Ensure all secrets are pulled dynamically from validated runtime environment variables or secure vault/database stores.

3. **API & Network Vulnerabilities (OWASP Top 10):**
   - **Open Endpoints:** Guard public endpoints (e.g., `/auth/register`) with strict rate limiting, CAPTCHA, or admin authentication to prevent resource exhaustion / DoS.
   - **CORS:** Disallow wildcard `allow_origins=["*"]` when `allow_credentials=True`.
   - **SSRF:** Validate and filter all outbound connector URLs (e.g. Prometheus, webhooks) to block private IP ranges (RFC 1918, `169.254.169.254`, `localhost`).
   - **Replay Attacks:** Enforce HMAC-SHA256 signature verification and timestamp expirations on inbound webhooks.

4. **AI / LLM Threat Modeling (OWASP LLM Top 10):**
   - **Prompt Injection:** Treat all inbound telemetry, logs, and alert summaries as untrusted user input. Use strict XML boundary fences (`<telemetry_evidence>`).
   - **Action Proposal Governance:** Never let the LLM execute arbitrary shell or container commands. Actions must be restricted to a strict, typed enum validated against deterministic safety policy gates and cryptographic proposal hashes.
   - **Hallucination & Citation Forgery:** Validate that all cited evidence IDs exist in real telemetry before accepting confidence scores.

## Operational Protocol
In every response where architecture, endpoints, or code are touched, include a dedicated **🛡️ Red Team Security Review** summarizing:
- Exploitable loopholes identified
- Severity and attack vector
- Concrete defensive remediations applied
