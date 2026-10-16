---
name: critical-rca-only
description: Root-cause-only playbook for critical severity incidents. Use whenever an alert has severity critical, for any anomaly type; produces hypotheses, blast radius and timeline, and never proposes or executes remediation actions.
metadata:
  version: "2"
  priority: 95
  triggers:
    severities: [critical]
---
# Critical incident: root cause only

The on-call SRE owns the response. Your job is a fast, evidence-backed RCA.

## Rules
- Do NOT propose, request or execute any remediation action. `recommended_actions` must be empty.
- Provide 2-3 competing hypotheses, each with supporting and contradicting evidence (cite E1..En).
- State the blast radius (affected services).
- Give a timeline of related changes (deploys, config changes) from the evidence.
- Name the single best next diagnostic step (read-only).
- State confidence honestly. A low-confidence RCA with clear gaps beats a confident guess.
- Always escalate to a human immediately; the brief is advisory context.
- Do not rely on [SIMULATED] evidence as proof.

## Output
- issue: service, anomaly type, critical.
- evidence: tagged E1..En.
- root cause: ranked hypotheses with confidence.
- what to do: "Escalated to on-call; no automated action." plus the next diagnostic step.
- open questions: evidence gaps.

## Example
- hypotheses: (1) v42 deploy regression, supports E2, contradicts none (medium); (2) DB lock contention, supports E4, contradicted by E5 (low).
- blast radius: checkout, payments.
- next step: compare error logs before and after the 14:02 deploy.
- recommended_actions: [] (escalated).
