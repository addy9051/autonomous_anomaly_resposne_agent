# Adapter-First Evidence Layer — Implementation Log

Living document. Tracks the agreed plan and what has actually been implemented.
Strategy source of truth: [SRE_AGENT_PRODUCT_STRATEGY_BLUEPRINT.md](./SRE_AGENT_PRODUCT_STRATEGY_BLUEPRINT.md)
(it supersedes the polling and "zero data duplication" claims in [SRE_STRATEGIC_BLUEPRINT.md](./SRE_STRATEGIC_BLUEPRINT.md)).

## Decision

Adapters (Prometheus, Datadog, Splunk) are the **read-only evidence layer**, queried on demand.
Investigations are **triggered by alerts**, not by continuous adapter-to-LLM polling.
Live adapters never fabricate data; mock data exists only in `SimulatorAdapter`.

## Plan and status

| # | Step | Status |
|---|------|--------|
| 1 | Adapters fail loudly outside simulator mode; fix Splunk TLS | Done (commit `59c2688`) |
| 2 | Alert-triggered intake (Alertmanager first) | Done (not yet committed) |
| 3 | Remove/rewire fabricated monitoring tools; expand `gather_context` into a scoped timeline | Done (not yet committed) |
| 4 | Provenance fields on normalized records; deploy-events source | Pending |
| 5 | Hypothesis modelling | Pending |

## Step 1 — Adapters fail loudly

- `TelemetrySourceError` added in `data_pipeline/connectors/adapters/base.py`.
- Datadog and Splunk raise it on missing credentials, non-200 responses and network errors (Datadog also on parse errors). Prometheus raises on non-200 and network errors in every environment. The old mock records are removed.
- `stream_signals` loops in Splunk and Prometheus catch the error and keep polling.
- Splunk TLS verification is on by default. New settings `splunk_verify_ssl` and `splunk_ca_bundle` in `shared/config.py` allow self-signed dev instances.
- Splunk search window now honours `time_window_seconds`.
- `get_telemetry_adapter` raises `ValueError` for an unknown mode instead of falling back to the simulator.
- Verifier excludes records tagged `simulated_mock` from recovery evidence.
- Tests: `tests/unit/test_telemetry_adapters.py` rewritten for the new contract.

Consumers: the verifier turns a raised connector error into `INCONCLUSIVE`; `gather_context` in `agents/diagnosis/graph.py` records it as a missing source.

## Step 2 — Alert-triggered intake (Alertmanager)

- Endpoint: `POST /api/v1/alerts/alertmanager` in `api.py` (existing auth dependency; `dry_run` query param, default `true`).
- Parser: `data_pipeline/connectors/alertmanager_intake.py` converts each **firing** alert with a service-identifying label into an `AnomalyEvent`. Resolved and unscoped alerts are skipped. Fingerprint, labels, annotations, start time and generator URL are preserved in `raw_event`.
- Orchestrator: `process_event` now detects, then calls the new `process_anomaly`. Alerts call `process_anomaly` directly, bypassing the monitoring agent but still passing dedupe, incident creation, diagnosis, the policy gate and approval.
- Tests: `tests/unit/test_alert_intake.py`, `tests/unit/test_alert_webhook_api.py`.

### Known limitations

- Alertmanager cannot sign AWS IAM requests; production needs a gateway path that injects the trusted identity header (Lambda authorizer or signing proxy). Not wired.
- Anomaly type is a keyword guess from the alert name and summary; unknown alerts default to `error_rate`.
- The endpoint runs diagnosis inside the request. It should move to a background task before real use, since Alertmanager webhook timeouts are short.
- Dedupe uses the service-plus-type key, not the Alertmanager fingerprint.
- `import orchestrator` fails in a bare interpreter on `langfuse.callback` (pre-existing; tests are unaffected).

## Step 3 — No fabricated evidence; scoped timeline

- `agents/monitoring/tools/monitoring_tools.py`:
  - `prometheus_query`: synthetic fallback removed; failures return `{"evidence_gap": true}`.
  - `kafka_lag_inspector`, `fraud_signal_fetch`: deleted (random data, no real source).
  - `baseline_compare`: baseline now computed from 7 days of adapter history (`compute_baseline_result`); fewer than 20 samples, an adapter error, or a simulated source yields an evidence gap, not hard-coded numbers.
  - `anomaly_classifier`: kept in the file but no longer registered for the LLM agent, since it trains on a synthetic reference distribution.
  - `ALL_MONITORING_TOOLS` is now `prometheus_query` and `baseline_compare`; `agents/monitoring/prompts.py` updated, including a rule never to invent values for gaps.
- `agents/diagnosis/evidence.py` (new): `collect_evidence` queries the adapter per service (up to 3) over a 15-minute window, returns a time-sorted timeline, flags `simulated` records, and reports gaps as `<source>:<service>:<metric|*>` for failed sources and for requested metrics that returned no samples. One failing service no longer drops the others.
- `agents/diagnosis/graph.py`: `gather_context` uses `collect_evidence`.
- `PrometheusAdapter.fetch_signals` now runs a range query over `time_window_seconds` (previously an instant query that ignored the window).
- Tests: `test_tools.py` rewritten, new `test_evidence_collection.py`. Full unit suite: 113 passed.

### Limitations
- Timeline is metrics only; logs (Splunk) and deploy events are not yet pulled (step 4).
- The window is a fixed 15 minutes, not anchored on the alert's `starts_at`.
- The simulated-flag check relies on adapters tagging `simulated`; Datadog/Splunk range behaviour for `fetch_signals` was not changed.
- The monitoring agent (`process_event` path) is no longer given Kafka-lag or fraud signals; those detection criteria were removed rather than backed.

## Open audit items (not yet scheduled)

- `orchestrator.main()` stream mode reads `args.dry_run`, but no such argument exists.
- Slack summary LLM call in the action agent is unguarded.
- Several GET endpoints in `api.py` are unauthenticated.
- `MemorySaver` is used where the docs say Redis.
- Docs mention CrewAI.
- `trigger_workflow` returns `mocked` without `N8N_API_KEY`.
- Monitoring tools (`kafka_lag_inspector`, `fraud_signal_fetch`, `baseline_compare`, `prometheus_query` fallback) fabricate data (addressed by step 3).
