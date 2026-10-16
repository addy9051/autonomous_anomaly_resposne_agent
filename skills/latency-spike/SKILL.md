---
name: latency-spike
description: Playbook for latency spikes in p95 or p99 response time. Use when the anomaly type is latency_spike at high, medium or low severity; separates application, database and network causes and lists allowed automated actions.
metadata:
  version: "2"
  priority: 60
  triggers:
    anomaly_types: [latency_spike]
    severities: [high, medium, low]
---
# Latency spike

## Gather
- p99/p95 latency and error rate of the affected service over 15 minutes.
- Recent deploys.
- Downstream dependency latency (database, cache).
- Saturation (CPU, connections).

## Reason
- Rose with a deploy: application.
- Rose with DB connections or locks: database.
- Rose across many services at once: network or infrastructure.

## Actions
- Allowed automated actions (non-critical only): `scale_replicas` (1-10), `clear_cache`, `restart_unhealthy_pod`.
- Everything else is recommend-only or needs SRE approval. Rollbacks are human-only.

## Escalate
If scaling does not recover p99 within the stabilisation window, or latency is global.

## Output
- issue, evidence (E1..En), root cause with confidence, actions taken, or the top 2-3 expert steps in order each with its rollback, open questions.

## Example
- evidence: E1 p99 120ms to 900ms; E2 CPU 95%; E3 no deploys.
- root cause: CPU saturation (medium).
- done: scale_replicas 3 to 5; rollback is scaling back to 3.
