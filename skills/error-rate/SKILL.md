---
name: error-rate
description: Playbook for rising 5xx or 4xx error rates. Use when the anomaly type is error_rate at high, medium or low severity; lists allowed automated actions and keeps rollbacks human-only.
metadata:
  version: "2"
  priority: 60
  triggers:
    anomaly_types: [error_rate]
    severities: [high, medium, low]
---
# Error-rate increase

## Gather
- 5xx vs 4xx split and an error log summary.
- Deploy events in the last hour.
- Dependency health.
- Pod restarts and OOM kills.

## Reason
- Step change aligned with a deploy: suspect the release.
- Gradual rise with resource pressure: suspect saturation.
- 401/403 spike: suspect security; treat as a security signal.

## Actions
- Allowed automated actions (non-critical only): `restart_unhealthy_pod`, `update_circuit_breaker`, `clear_cache`.
- Rollbacks are always human-only. Recommend `rollback_deployment` with evidence; never execute it.
- Propose nothing else.

## Escalate
If errors persist after one automated action, or a rollback is the likely fix.

## Output
- issue, evidence (E1..En), root cause with confidence, actions taken or recommended (with rollback), open questions.

## Example
- evidence: E1 5xx 0.2% to 6% at 14:03; E2 deploy v42 at 14:02.
- root cause: release regression (medium).
- to do: recommend rollback_deployment for human approval; escalate.
