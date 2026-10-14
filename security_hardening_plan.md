# Security Hardening & Blocker Remediation Plan (v2)

> Updated: Auth approach changed to **API Gateway offload** per your preference.

---

## Blocker 1 — Bind Approval to Stored Proposal

### Problem
[`api.py:L448-L460`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/api.py#L448-L460) — Approval accepts caller-supplied `action_name`/`params` and permits `RECOMMENDATION_READY` status to reach execution.

### Changes

**[`api.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/api.py)**
- Remove `action_name`, `params` from `ApproveActionRequest` — the endpoint resolves action exclusively from the stored incident proposal
- Remove `RECOMMENDATION_READY` from `valid_pending_statuses`
- Add a `proposal_hash` check: compute a hash of the proposed action+params at proposal time, verify it matches before execution

**[`agent.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/agents/action/agent.py)**
- In `execute_approved_action()`, re-run `DeterministicActionPolicy.evaluate()` before calling `trigger_workflow()` — environment/params may have changed since proposal

---

## Blocker 2 — API Gateway Offload Authentication

### Architecture
```
Caller → AWS API Gateway (IAM SigV4) → sets X-Authenticated-User → FastAPI (trusts header)
```

### Changes

**[`api.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/api.py)**

```python
TRUSTED_IDENTITY_HEADER = "X-Authenticated-User"

async def verify_auth(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> str:
    """
    Extract authenticated identity from trusted gateway header.
    
    In production: requires X-Authenticated-User set by API Gateway.
    In development: falls back to API key or allows unauthenticated with warning.
    """
    settings = get_settings()
    env = settings.app.app_env.lower()
    
    # 1. Prefer gateway-provided identity (trusted in all environments)
    identity = request.headers.get(TRUSTED_IDENTITY_HEADER)
    if identity:
        return identity
    
    # 2. Non-development: fail closed
    if env != "development":
        raise HTTPException(
            status_code=401,
            detail=f"Missing {TRUSTED_IDENTITY_HEADER} header. "
                   "Requests must pass through the authenticated API Gateway."
        )
    
    # 3. Development fallback: API key → identity mapping
    auth_token = settings.app.api_auth_token
    if auth_token and api_key == auth_token:
        return "dev-api-key-user"
    
    # 4. Development with no auth configured: warn and allow
    logger.warning("auth_bypassed_development", 
                   tip="Configure API_AUTH_TOKEN or route through gateway")
    return "anonymous-dev"
```

Apply `Depends(verify_auth)` to **all mutating endpoints**:
| Route | Currently Protected |
|---|---|
| `POST /api/v1/events/process` | ❌ → ✅ |
| `POST /api/v1/incidents/{id}/approve` | ✅ (keep) |
| `POST /api/v1/demo/run` | ❌ → ✅ |
| `POST /api/v1/demo/scenarios/{name}/run` | ❌ → ✅ |
| `POST /api/v1/stream/start` | ❌ → ✅ |
| `POST /api/v1/knowledge/seed` | ❌ → ✅ |

**`ApproveActionRequest`** — remove `approver` field. The approver identity comes from `verify_auth()`:
```python
class ApproveActionRequest(BaseModel):
    approved: bool = True
    # approver: REMOVED — derived from gateway-authenticated identity
    dry_run: bool = False
    reason: str | None = None
```

**[`config.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/shared/config.py)**
- Add `trusted_identity_header: str = "X-Authenticated-User"` to `AppConfig`
- Add `require_gateway_auth: bool = True` (overridable for development)

---

## Blocker 3 — Verification Integrity

### Changes

**[`verifier.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/agents/verification/verifier.py)**

1. **Reject simulator evidence when `dry_run=False`**:
   - Filter records where `connector_source == "simulator"` or `tags.simulated == True`
   - If no authoritative records remain → `INCONCLUSIVE`

2. **Require minimum signal coverage**:
   ```python
   REQUIRED_SIGNALS = {"error_rate", "latency_p99"}
   ```
   If any required signal is missing → `INCONCLUSIVE`

3. **Freshness check**: Records older than `2 × time_window_seconds` → stale → `INCONCLUSIVE`

4. **Configurable thresholds from policy criteria**:
   - Accept `verification_criteria: dict` parameter
   - Use policy-provided `error_rate_threshold` / `latency_threshold_ms` if present
   - Fall back to conservative defaults (1%, 1000ms)

**[`agent.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/agents/action/agent.py)**
- Change `stabilization_window_seconds=0.1` to read from config
- Pass `verification_criteria` from the policy evaluation result through to verifier

**[`config.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/shared/config.py)**
- Add `stabilization_window_seconds: float = 30.0` to `AgentConfig`
- Add `stabilization_window_seconds_dry_run: float = 0.1`

---

## High — Policy Validation Gaps

**[`policy.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/agents/action/policy.py)**
- Add required-params validation for all workflow-registered actions, cross-referencing `N8N_WORKFLOWS`
- Align parameter names (e.g. `failure_rate_threshold` → `threshold` to match workflow)
- Deny actions with missing required parameters

---

## High — Gate Synthetic Heartbeat

**[`api.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/api.py)**
- Only start `pulse_heartbeat()` when `APP_ENV == "development"` or `ENABLE_SYNTHETIC_HEARTBEAT=true`

---

## Medium — Escalated Incidents

**[`incident_store.py`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/shared/incident_store.py)**
- Add `IncidentStatus.ESCALATED.value` to `ACTIVE_STATUSES`

---

## Medium — Docker Compose Passwords

**[`docker-compose.yml`](file:///d:/Downloads/projects/autonomous_anomaly_resposne_agent/docker-compose.yml)**
- Replace `agent_secret_2024` with `${POSTGRES_PASSWORD:-dev_only_not_for_production}`
- Add comment: `# MUST override in production`

---

## New File — `docs/DEPLOYMENT_SECURITY.md`

Documents:
- API Gateway + IAM setup requirements
- VPC placement (private subnets, no public exposure of FastAPI)
- Header trust chain (`X-Authenticated-User`)
- Per-route permission mapping
- Agent workload IAM role scoping
- Development vs. production auth behavior

---

## Files Modified

| File | Summary |
|---|---|
| `api.py` | Gateway auth, auth on all mutating routes, bind approval to stored proposal, gate heartbeat |
| `agents/action/agent.py` | Re-run policy before execution, configurable stabilization |
| `agents/action/policy.py` | Complete parameter validation |
| `agents/verification/verifier.py` | Reject simulator evidence, freshness, coverage, configurable thresholds |
| `shared/config.py` | Auth config, stabilization config |
| `shared/incident_store.py` | Add ESCALATED to active statuses |
| `docker-compose.yml` | Env var substitution for passwords |
| `docs/DEPLOYMENT_SECURITY.md` | *(new)* Gateway + IAM deployment guide |
| `tests/integration/test_integration.py` | New tests for all changes |
