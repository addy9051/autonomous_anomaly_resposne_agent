---
name: security-and-fraud
description: Playbook for security and fraud signals such as credential stuffing, scraping, DDoS, auth failure spikes and rising fraud scores. Use when the anomaly type is security or fraud_signal; recommend-only, always escalated to security on-call.
metadata:
  version: "2"
  priority: 70
  triggers:
    anomaly_types: [security, fraud_signal]
---
# Security / fraud signal

## Gather
- Auth failure rate.
- Request rate by source.
- Geographic and user-agent spread.
- Access to sensitive endpoints.
- Fraud score trend.

## Reason
Separate abuse (scraping, credential stuffing, DDoS) from a misconfiguration or a legitimate traffic change. Cite E1..En; [SIMULATED] evidence is not proof.

## Actions
- Allowed automated actions: none.
- Security-impacting changes (blocking, firewall, throttling a merchant) are recommend-only and need a human decision.

## Escalate
Always to the security on-call. Include the evidence and the recommended containment steps with their blast radius.

## Output
- issue, evidence, root cause with confidence, recommended containment (each with blast radius and rollback), open questions.

## Example
- evidence: E1 401s 50x baseline; E2 80% from 3 IP ranges; E3 one user-agent.
- root cause: credential stuffing (medium).
- recommend: block the 3 ranges at the edge (blast radius: legitimate users on those ranges); escalate to security on-call.
