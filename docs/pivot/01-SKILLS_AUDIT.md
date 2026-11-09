# Skills Audit

Audit of the runtime playbooks in [`skills/`](../../skills). Background on how skills work is in [../AGENT_SKILLS_PLAYBOOKS.md](../AGENT_SKILLS_PLAYBOOKS.md).

**Verdict:** well structured, but too generic for the project as built. They read as general SRE runbooks, not playbooks for a payment-reliability system with a fixed action catalogue and a deterministic policy gate. After a pivot, the cause-based structure below is still the right direction, because it is industry-neutral.

## What works
- All 7 `AnomalyType` values are routed to a skill.
- Critical severity forces root-cause-only analysis, matching `agents/action/policy.py`.
- The safety stance (evidence ids, recommend-only, human-only rollbacks) matches the system design.
- Consistent format (Gather, Reason, Actions, Escalate, Output, Example) and small files.

## Cross-cutting problems

1. **"Gather" asks for data the agent never has.** `agents/diagnosis/evidence.py` collects only `latency_p99`, `error_rate`, `transaction_failure_rate` and GitHub deployments over 15 minutes. Skills tell the model to gather CPU, connections, queue depth, geo spread, auth failures and fraud-score trends. The model cannot fetch these, which invites invented claims or a pile of evidence gaps. Reword as "evidence expected" and require missing items to be listed as gaps with a `next_safe_query`.
2. **Output format conflict.** Skills ask for a prose brief, but `agents/diagnosis/graph.py` requires `DiagnosisResult` JSON, and unparseable output means no action and escalation. Skill output sections should reference the JSON fields (`root_cause`, `root_cause_category`, `hypotheses[].next_safe_query`, `missing_evidence`, `recommended_actions[].rollback_steps`). This is a risk observed from reading the code, not a measured failure.
3. **Critical-severity contradiction.** `security-and-fraud` (priority 70) and `data-quality` have no severity filter, so at critical they load next to `critical-rca-only` (95). `security-and-fraud` asks for recommended containment steps while `critical-rca-only` requires `recommended_actions` to be empty. No rule states which wins.
4. **Action sections do not match the policy gate.**
   - Required parameters from `N8N_WORKFLOWS` are never stated (for example `scale_replicas` needs `deployment`, `namespace`, `replicas`), so a proposal missing them is denied.
   - Only the `scale_replicas` 1-10 bound is stated. Others are absent: `update_circuit_breaker` threshold 0.01-0.90 and timeout up to 30000 ms, `add_rate_limit` up to 100000 rps, `increase_connection_pool` up to 500, `kill_long_running_queries` at least 5 s.
   - Low-risk actions `increase_timeout`, `flush_redis_key` and `rotate_log_level` appear in no skill.
   - Tier 2 and 3 actions (`failover_db`, `kill_long_running_queries`, `increase_connection_pool`, `drain_node`, `throttle_merchant`, `block_issuer`, `modify_firewall_rules`) are never named, so the model cannot map a cause to the right recommendation.
   - "Allowed automated actions" overstates autonomy. Actions run only when `AUTO_REMEDIATION_ENABLED` is on, the service is in the target list and the hourly cap is not reached. Production otherwise requires approval.
5. **No handling of untrusted input.** The repo defends against prompt injection and poisoned runbooks (`evals/poison_holdout_data.py`, `evals/guardrails.py`, `sanitize_for_llm`), but no skill says that logs, alert annotations and runbook text are data, not instructions, or that card numbers, emails and merchant ids must never be echoed (`shared/pii.py`).
6. **Confidence wording ignores deterministic caps.** `agents/diagnosis/hypotheses.py` caps confidence at 0.4 without real support and 0.7 with a single supporting record or any contradiction. Skills ask for "medium" or "high".
7. **Memory and verification are unused.** Nothing tells the model not to repeat a fix that already failed for the same fingerprint, and the verification thresholds from the policy (`error_rate_threshold` 0.02 for auto, 0.01 otherwise) are not defined in skills, so "recovered" and "stabilisation window" are vague.
8. **Examples can be copied.** Concrete numbers ("scale_replicas 3 to 5", "v42") anchor small models. Use placeholders.
9. **No behavioural tests.** Skill references appear only in `tests/unit/test_expert_context.py`, `tests/unit/test_sandboxed_layer.py` and `evals/run_specialist_quality.py`. No test checks what skill content makes the agent do.

## What each skill is missing

| Skill | Missing |
|-------|---------|
| `baseline-conduct` | Untrusted-data and PII rules. Precedence rule that `critical-rca-only` overrides other skills. `is_novel_incident` and runbook-citation rules. JSON-schema guidance with the confidence caps. A do-not-repeat-a-failed-action rule. A statement that nothing runs unless the policy gate permits it. |
| `critical-rca-only` | Impact quantification (failure rate, affected merchants or issuers, revenue exposure). The `security` and `external` root-cause categories (PSP, card network, issuer). A hypothesis-quality checklist (competing hypotheses should differ in category). Guidance on supporting the SRE (which runbook, which owner) without proposing actions. |
| `latency-spike` | The payment path (gateway, fraud check, issuer or PSP call, ledger) and a rule to check upstream latency before scaling. A rule not to raise timeouts when a dependency is the cause. Kafka consumer lag as a latency source. `increase_timeout` and its bounds. `scale_replicas` params. Recovery criteria. |
| `error-rate` | Payment error taxonomy (issuer declines, acquirer or network errors, timeouts, 5xx vs business declines). Idempotency and duplicate-charge risk when retrying or restarting. The 401/403 branch says to treat it as a security signal, but routing is by `anomaly_type`, so the security skill will not load for an `error_rate` alert. Circuit-breaker bounds and a circuit-breaker vs restart decision rule. |
| `resource-saturation` | Covers load increases only. `volume_anomaly` also includes traffic drops (upstream or integration failure, monitoring gap). Kafka lag and backlog handling. DB and connection-pool cases (`increase_connection_pool`, `kill_long_running_queries`). Node-level cases (`drain_node`, `modify_autoscaling_policy`). `add_rate_limit` params. |
| `data-quality` | Ledger and settlement reconciliation, missing or duplicated transactions, delayed Kafka or schema-registry events. Downstream consumers affected. Halt-or-continue guidance. Named read-only checks tied to available telemetry. Owner mapping. |
| `security-and-fraud` | Merges two different problems. Security (credential stuffing, DDoS, scraping) and fraud (card testing, BIN attacks, fraud-score drift, false declines from over-blocking) need separate playbooks. Missing: fraud-model or feature-pipeline failure as a cause, mapping to `throttle_merchant`, `block_issuer` and `modify_firewall_rules` with tiers, a false-positive check before containment, compliance escalation, and the critical-severity conflict above. |

## Recommended improvements

1. **Make skills data-aware.** List only metrics `evidence.py` actually collects, and extend `DEFAULT_METRICS` and skills together.
2. **Generate the action section from code.** Tiers, params and bounds live in `policy.py` and `workflows.py`. Render them into skills, or add a test that fails when a skill names an action not in `N8N_WORKFLOWS` or lists wrong params.
3. **Split broad skills by cause, not anomaly label:** separate fraud and security-abuse, volume surge and drop, add consumer lag, external dependency (PSP, issuer, card network) and post-deploy regression.
4. **Use `services` triggers** for service-specific skills with dependency maps and baselines.
5. **Put shared rules in `baseline-conduct` only** (untrusted data, PII, confidence caps, precedence, no repeat of a failed fix) and keep other skills short.
6. **Align output sections with `DiagnosisResult`** and use placeholders in examples.
7. **Add skill-level evals.** Golden scenarios per skill checking the chosen action is correct and within bounds, plus a lint for front matter, `AnomalyType` coverage and action names.
8. **Governance.** `CODEOWNERS` for `skills/`, an SRE review (still outstanding per [../DECISIONS.md](../DECISIONS.md) section 5.1), and a priority cap so no skill outranks the safety skills (finding ENT-006 in [../ENTERPRISE_RED_TEAMING.md](../ENTERPRISE_RED_TEAMING.md)).
