---
name: data-quality
description: Playbook for data quality anomalies such as schema changes, volume drops, null-rate spikes and failed ingestion jobs. Use when the anomaly type is data_quality; recommend-only, no automated actions.
metadata:
  version: "2"
  priority: 60
  triggers:
    anomaly_types: [data_quality]
---
# Data quality anomaly

## Gather
- Schema, volume and null-rate changes against baseline.
- Upstream job status.
- Recent pipeline or schema deploys.

## Reason
Decide which applies: the source changed, an ingestion job failed, or a transform regressed. Cite evidence ids (E1..En).

## Actions
- Allowed automated actions: none. Data repair is recommend-only.
- Propose the safe read-only verification query first (for example a row count or null-rate comparison before and after the change).

## Escalate
To the data owner of the affected dataset.

## Output
- issue, evidence, root cause with confidence, what to do (verification query then repair proposal), open questions.

## Example
- evidence: E1 null rate of customer_id 0.1% to 38%; E2 schema deploy at 09:10.
- root cause: transform regression after the schema change (medium).
- to do: run a null-rate comparison per source partition; escalate to the data owner.
