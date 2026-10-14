## Overall assessment

The code has useful building blocks—telemetry adapters, a diagnosis graph, runbook retrieval, action tiers, and incident UI—but the end-to-end behavior **doesn’t yet match the updated blueprint’s core promise**. In particular, it does not reliably gather evidence for diagnosis, the approval path is not a safe execution flow, and an action response can be treated as incident recovery without independent verification.

I reviewed the main paths affected by the blueprint: API, orchestration, monitoring, diagnosis, action execution, schemas, adapters, feedback, UI, and integration tests. I did not audit every infrastructure manifest or styling file, and I made no changes.

## Change these first

### P0 — Make production writes opt-in and fail closed

At present, recommendations can reach action execution through the orchestrator. Tier 1 actions are classified as autonomous, and Tier 2 actions auto-approve in development. The API also exposes demo and streaming routes where the caller can request `dry_run=false`. `orchestrator.py` · `tiers.py` · `agent.py` · `api.py`

For the blueprint’s initial product stages:

- Default all production actions to **recommend-only**.
- Require an explicit, deterministic policy decision before any write.
- Enforce policy by action, service, environment, target, and parameters—not just action name.
- Disable development auto-approval in any deployment that can reach real infrastructure.
- Keep simulation mode visibly separate from live execution; never represent a simulated result as a real action or recovery.
- Add authentication and authorization to sensitive endpoints, especially action approval and routes that can trigger processing. I don’t see route-level authentication in the FastAPI code; an upstream gateway might provide it, but don’t assume that without verifying deployment configuration.

### P0 — Repair the approval and incident lifecycle

The approval lifecycle currently has a broken handoff:

- The action agent uses `AWAITING_APPROVAL`, while the approval endpoint expects `ACTION_PENDING`. `agent.py` · `api.py`
- The orchestrator removes the incident from the active map after processing—even if it awaits approval—so the approval endpoint may no longer find it. `orchestrator.py`
- The endpoint marks the action “executed” and the incident resolved on approval; it does not execute the action or verify recovery. `api.py`

Implement an explicit lifecycle such as:

```text
investigating → recommendation_ready → awaiting_approval
→ executing → verifying → verified_recovered
                              ↘ failed / inconclusive → escalated
```

Persist the incident while it is active. Make an approval reference a specific action and exact parameters, record who approved it, execute only after approval, then pass the result to verification. A rejection should be recorded and should not resolve the incident.

### P0 — Add independent recovery verification

Currently, the action agent marks the incident `RESOLVED` when every action result is `"success"`. That confirms the action call’s status, not that the service recovered. `agent.py`

Add a verifier that checks predeclared, service-level success criteria after a stabilization window. It should report **verified recovery**, **not recovered**, or **inconclusive**. Use user-facing health or synthetic checks where possible, alongside relevant SLO signals. If verification fails or cannot run, stop further automation and escalate—don’t mark the incident resolved.

### P0 — Remove action-shaped fallbacks

Two fallbacks can turn missing or malformed evidence into an apparently actionable diagnosis:

- If diagnosis output cannot be parsed, the code supplies a `scale_replicas` action. `graph.py`
- If the knowledge base has no usable result, diagnosis inserts synthetic runbooks. `graph.py`

Replace these with explicit states such as `diagnosis_unavailable`, `evidence_insufficient`, or `runbook_source_unavailable`. **No fallback or parse error should create an executable action.** Test that malformed model output and unavailable sources can only result in a non-executable recommendation or escalation.

## Then align investigation with the blueprint

### P1 — Connect the adapters to incident investigation

The universal adapters are instantiated, but I don’t see them queried in the main `process_event` flow. Diagnosis context gathering asks the model to retrieve metrics and recent changes, but its implementation only sends the anomaly to an LLM; it does not call those telemetry or deployment APIs. `orchestrator.py` · `graph.py` · `base.py`

Wire event-triggered, read-only queries into the investigation: relevant SLO metrics, a bounded log sample, deployment/configuration changes, and—where configured—runbooks and ownership. Keep the first release to one or two validated integrations rather than advertising broad connectivity that is not used in the diagnostic path.

Also make connector failure explicit. For example, the Prometheus adapter can return a synthetic record with value `0.0` after an exception, which could be mistaken for real telemetry. `prometheus_adapter.py` Distinguish mock data structurally and forbid it in live mode; propagate source errors as evidence gaps rather than normal values.

### P1 — Make the diagnosis evidence-based and uncertainty-aware

The diagnosis data contract has a confidence and a reasoning string, but it does not make source-linked evidence, contradictory observations, missing data, or hypothesis comparisons first-class. Specialist agents return empty evidence and a hard-coded confidence of `0.9`. `schemas.py` · `experts.py`

Add structured investigation records for:

- Competing hypotheses and affected scope.
- Supporting and contradicting evidence.
- Source, query/reference, event time, observation time, and freshness.
- Missing or degraded sources.
- The next safe query or human question.
- A confidence band with clear interpretation—not an uncalibrated model-generated number.

Treat the current expert outputs as generated analysis, not verified evidence, until they cite actual observations.

### P1 — Replace chain-of-thought storage/display with decision evidence

The diagnosis schema asks for a “full chain-of-thought,” and the UI labels and displays it as “Reasoning Chain.” `schemas.py` · `ActiveIncidents.tsx`

Change this to a responder-facing **decision summary**: conclusion, evidence references, assumptions, alternatives considered, uncertainty, and recommended next step. Don’t retain or expose private chain-of-thought as an audit record. Review what is sent to model providers, logs, Langfuse, and incident systems; define redaction and retention explicitly.

### P1 — Make incidents durable

Active incidents and resolved history are in-memory dictionaries/lists. They will not provide the durable incident thread, audit trail, or recoverable approval state described by the blueprint, and will be lost on process restart. `orchestrator.py`

Persist lifecycle transitions and the minimal necessary audit record: evidence references, policy decision, action parameters, approval identity, execution result, and verification outcome. Add retention controls; avoid default storage of raw telemetry.

## Defer or reshape these features

### P1 — Pause live reinforcement learning

The orchestrator sends outcomes into the feedback loop during incident processing, and the feedback agent trains the contextual bandit online. The reward includes MTTR and an LLM judge score. `orchestrator.py` · `agent.py` · `reward.py`

That conflicts with the blueprint’s recommendation to evaluate before granting autonomy: incidents may still be awaiting approval, failed, or unverified when scored. Start with curated scenarios, replay, reviewer feedback, offline policy evaluation, and shadow mode. Only score confirmed outcomes, and don’t allow learned policy to override deterministic safety rules.

### P1 — Narrow what “autonomous” means

The static tier table labels operations such as cache clearing, replica scaling, and circuit-breaker changes as automatically executable. `tiers.py` A tier alone doesn’t account for the target environment, current state, service-specific hazards, or parameters.

Replace static action tiers as the final authority with typed action definitions and a deterministic policy gate. Initially, let the model recommend the action and let the responder execute or approve it. Consider bounded automation only after the action has validated preconditions, limits, timeout, stop conditions, and verification criteria.

## Fix UI and tests to reflect the real lifecycle

The standalone UI filters for `ACTION_PENDING`, while backend incident statuses are serialized as lowercase values such as `awaiting_approval`. That can hide items in the approval queue. `app.js` Also, the integration test currently treats an action API success as a resolved incident without testing independent recovery verification. `test_integration.py`

Add lifecycle tests covering: approval actually executes the selected action; rejection does not execute; restart preserves pending approvals; failed or inconclusive verification does not resolve; malformed diagnosis cannot create a write action; and telemetry-source failures remain visible as missing evidence.

## Recommended implementation order

1. **Read-only mode by default; fail closed on model, connector, or parsing errors.**
2. **Correct and persist incident/approval state transitions.**
3. **Add the independent verifier and verified/inconclusive outcomes.**
4. **Wire one real telemetry path into an evidence-linked timeline.**
5. **Update schemas and UI for hypotheses, evidence, uncertainty, and safe recommendations.**
6. **Build replay and shadow-mode evaluations; keep online policy learning disabled.**
7. **Pilot one bounded, approval-required action only after those checks pass.**

One additional configuration item: the repository uses a fixed PostgreSQL password default in configuration and sample deployment settings. Replace that with a required, externally supplied secret before any real deployment; don’t treat a sample value as a production credential. `config.py` · `.env.example`