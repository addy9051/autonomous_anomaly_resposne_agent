---
name: resource-saturation
description: Playbook for resource saturation and traffic volume anomalies (CPU, memory, connection pool, queue depth, runaway clients, leaks). Use when the anomaly type is resource_saturation or volume_anomaly at high, medium or low severity.
metadata:
  version: "2"
  priority: 60
  triggers:
    anomaly_types: [resource_saturation, volume_anomaly]
    severities: [high, medium, low]
---
# Resource saturation / volume anomaly

## Gather
- CPU, memory, connection pool, queue depth.
- Request volume vs baseline.
- Autoscaler state.

## Reason
Distinguish organic traffic growth from a runaway client, or from a leak (steady climb without traffic growth). Cite E1..En.

## Actions
- Allowed automated actions (non-critical only): `scale_replicas`, `clear_cache`, `restart_unhealthy_pod`, `add_rate_limit`.
- Node drains and autoscaling policy changes need SRE approval; recommend only.
- Rollbacks are human-only.

## Escalate
If saturation is cluster-wide or scaling hits its cap.

## Output
- issue, evidence (E1..En), root cause with confidence, actions taken or recommended (with rollback), open questions.

## Example
- evidence: E1 memory climbs 2%/hour; E2 request volume flat.
- root cause: memory leak (medium).
- done: restart_unhealthy_pod on the highest-memory pod; recommend a heap profile.
