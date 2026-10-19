# Technical Architecture

This document describes what the code does today. For the reasoning behind each choice see [DECISIONS.md](./DECISIONS.md); for isolation and the AWS design see [DEPLOYMENT_SECURITY.md](./DEPLOYMENT_SECURITY.md); for evaluation see [LLM_EVALS.md](./LLM_EVALS.md).

Status key: **built** = in code and covered by tests; **mocked** = code path exists but the integration is simulated; **designed** = docs/Terraform only, never applied.

## 1. Summary

A sandboxed SRE intelligence layer. An alert starts an investigation: the system collects evidence, produces evidence-grounded root-cause hypotheses, and then, depending on incident severity and a deterministic policy, proposes or runs a limited set of reversible actions. It is a local demo, not a production operator.

- **Critical incidents:** root-cause analysis only. No action is proposed or executed (enforced in the orchestrator, `ActionAgent` and `DeterministicActionPolicy`).
- **Non-critical incidents:** Tier 1 reversible actions may auto-run if allowlisted and enabled; Tier 2 needs approval; Tier 3 is human-only.
- **Models:** open-weight models behind an OpenAI-compatible endpoint (vLLM; Modal in dev). The app only talks to `LLM_BASE_URL`.
- **Remediation executors are mocked.** n8n workflows return "mocked" without an API key.

## 2. Tech stack

| Area | Implementation |
| --- | --- |
| API | FastAPI (`api.py`, port 8010 in the Makefile) |
| Orchestration | `AgentOrchestrator` (`orchestrator.py`) |
| Diagnosis workflow | LangGraph, 4 nodes, in-memory `MemorySaver` checkpointer |
| LLM access | LangChain `ChatOpenAI` / `OpenAIEmbeddings` pointed at a self-hosted vLLM endpoint (`shared/llm.py`) |
| Egress control | Fail-closed host allowlist (`shared/egress.py`) |
| Knowledge base | PostgreSQL + pgvector; hybrid retrieval (see section 4) |
| Persistence | Postgres incident store, audit log, agent controls and episodic memory (`shared/incident_store.py`, `shared/episodic_memory.py`, migrations 001-004); in-memory backends for tests |
| Observability | OpenTelemetry tracing, structlog, self-hosted Langfuse, Prometheus |
| Frontend | Next.js dashboard (`dashboard/`) and a static HTML control page (`ui/`) |
| Evaluation | Deterministic checks, guardrails and an LLM judge (`evals/`), DeepEval retrieval A/B |
| Infra | `docker-compose.yml` (Postgres+pgvector, Prometheus, Langfuse), `infra/modal/vllm_app.py` (dev inference), `infra/aws/terraform` (**designed**, never applied) |

## 3. Pipeline

```
Alertmanager webhook / telemetry event / demo scenario
        |
        v
Monitoring Agent (telemetry path only)  -- Alertmanager alerts skip this step and map labels to severity
        |
        v
Episodic memory: fingerprint de-duplication
        |
        v
Diagnosis Agent (LangGraph)
  gather_context -> rag_runbook_lookup -> supervisor -> synthesise_rca
        |
        v
Severity branch
  critical     -> RCA only, no action
  non-critical -> Action Agent -> DeterministicActionPolicy -> tier handling
        |
        v
Recovery Verifier (after any executed action; fails closed to INCONCLUSIVE)
        |
        v
Incident store + audit log + regression detector
```

### 3.1 API and orchestration (`api.py`, `orchestrator.py`) - built
- Routes cover health (`/health`, `/api/v1/health/detailed`), intake (`/api/v1/events/process`, `/api/v1/alerts/alertmanager`), demo scenarios, streaming, connectors (list/select/credentials/test/activate/deactivate), incidents (list, active, detail, brief, approve, feedback), audit, agent controls (`GET/PUT /api/v1/agent/controls`), episodes, action tiers, metrics and the knowledge base (`seed`, `search`, `health`).
- `AgentOrchestrator.process_event` / `process_anomaly` run the pipeline above; `run_streaming` and `run_batch` drive synthetic telemetry.
- Approvals are claimed atomically from the store and bound to a proposal hash (`compute_proposal_hash` in `agents/action/policy.py`).

### 3.2 Monitoring Agent (`agents/monitoring/`) - built
- A LangChain tool-calling loop over `TelemetryEvent`s, with PII masking first. It emits an `AnomalyEvent` when confidence meets the threshold (default 0.75).
- Tools: `prometheus_query`, `baseline_compare` (Z-score against rolling baselines) and `anomaly_classifier` (scikit-learn Isolation Forest).
- Severity on this path is proposed by the LLM; there is no deterministic business-impact rubric. On the Alertmanager path severity comes from the alert label (`data_pipeline/connectors/alertmanager_intake.py`, missing or unknown maps to MEDIUM).

### 3.3 Diagnosis Agent (`agents/diagnosis/`) - built
- Graph: `gather_context` -> `rag_runbook_lookup` -> `supervisor` -> `synthesise_rca`.
- `supervisor` dispatches the specialists (`DatabaseExpert`, `NetworkExpert`, `SecurityExpert`, `ApplicationExpert` in `experts.py`). Each retrieves from its own collection plus the shared `policy` collection through the same `HybridSearchService`.
- `evidence.py` and `hypotheses.py`: evidence gets citable ids; invented citations are dropped; confidence is capped at 0.4 with no real support and at 0.7 with a single record or any contradiction. A failing data source is recorded as an evidence gap, never filled with mock data. These caps are heuristics, not calibrated probabilities.
- Prompt "skills" for each incident class live in `skills/*/SKILL.md` and are loaded by `shared/skills.py`.

### 3.4 Action Agent (`agents/action/`) - built, executors mocked
- `tiers.py`: Tier 1 reversible actions, Tier 2 approval-required, Tier 3 human-only. Unknown actions default to Tier 3.
- `policy.py` (`DeterministicActionPolicy.evaluate`): critical is always denied; unregistered workflows are denied; parameters are validated against typed bounds; high-risk actions are recommend-only; Tier 1 actions may auto-run only if allowlisted, `AUTO_REMEDIATION_ENABLED` is set, the service is in `AUTO_ACTION_TARGET_SERVICES` and the hourly limit is not reached; everything else needs approval in production.
- `workflows.py`: n8n workflow registry and webhook client. `pagerduty.py`: PagerDuty client for Tier 3 escalation. Both are inactive without credentials and the egress allowlist.

### 3.5 Recovery verification (`agents/verification/verifier.py`) - built
- Independently re-reads signals after an executed action and returns recovered, not recovered or INCONCLUSIVE. Stale, simulated or missing signals fail closed to INCONCLUSIVE.

### 3.6 Feedback and regression (`agents/feedback/regression.py`, `evals/`) - built
- `RegressionDetector` flags a new incident that recurs after a previously "recovered" one.
- There is no reinforcement-learning or online-policy component. Human feedback is recorded through `POST /api/v1/incidents/{id}/feedback`; the judge-vs-human gate in `evals/` has no human labels yet, so it is **not** passing.

### 3.7 Data pipeline (`data_pipeline/`) - built
- `connectors/adapters/`: Prometheus, Datadog and Splunk adapters plus a simulator, selected by `factory.py`, with query templates and deploy-event ingestion.
- `alertmanager_intake.py`: Alertmanager payload to `AnomalyEvent`, with keyword anomaly-type classification.
- `synthetic_producer.py` and `story_simulation.py`: synthetic telemetry and scenarios for demos and tests. All data in the demo is synthetic.
- `flink_jobs/anomaly_features.py`: sliding-window feature extraction and alert de-duplication, run in-process (no Flink cluster).
- `shared/pubsub.py`: Pub/Sub wrapper used by the synthetic producer; not part of the production design.

## 4. Knowledge base and retrieval (`knowledge_base/`) - built

- **Ingestion** (`ingestion/pipeline.py`): chunks runbooks (512 characters, 64 overlap), embeds them and upserts into the `documents` table with `collection` and `service_tags`. Eight sample runbooks are built in; `ingest_directory` loads `<root>/<collection>/*.md`.
- **Retrieval** (`retrieval/search.py`, `HybridSearchService`):
  1. Dense vector search (pgvector cosine), up to `rag_candidates_per_leg` (50) results.
  2. Sparse Okapi BM25 (`retrieval/bm25.py`), scored in-process over the whole table, up to 50 results.
  3. FlashRank cross-encoder (`retrieval/rerank.py`, `ms-marco-TinyBERT-L-2-v2`, local ONNX) scores the union of both candidate sets.
  4. Weighted Reciprocal Rank Fusion (k=60) over the vector, BM25 and reranker rankings; the top `rag_top_k` (5) are returned as `RunbookReference`s.
  - If reranking is disabled or fails, it falls back to two-way RRF. The FlashRank model must be pre-provisioned in `rag_rerank_cache_dir` because the egress allowlist blocks downloads.
- **Limits:** BM25 loads the whole table per query (fine for a small runbook set, not at scale); the unused `pg_trgm` indexes from migrations 001 and 002 remain; retrieval quality is only measured on a small engineer-written query set (`evals/retrieval_ab.py`).
- Runbooks are reference material, not permission to act; only the action policy authorises actions.

## 5. Isolation and safety - built (application level)

- `shared/egress.py`: private ranges are always allowed; public hosts need `EGRESS_ALLOWED_HOSTS`. Applied to the LLM client and every HTTP client, with a startup self-test.
- `shared/pii.py`: regex PII masking before model calls.
- Deterministic action policy and approval hashing (section 3.4).
- There is no OS-level, container or tenant isolation. The private AWS VPC (no internet route, VPC endpoints) is **designed**, not applied. In dev, Modal endpoints are public HTTPS protected by an API key, acceptable for synthetic data only.

## 6. Known gaps

- Remediation executors (n8n, PagerDuty, Slack) are mocked or inactive by default.
- Severity on the telemetry path is LLM-proposed, with no deterministic impact rubric.
- The AWS stack has never been applied; the Terraform assumptions need reconciling with the Postgres incident store.
- No human-labelled judge gate and no calibrated confidence.
- The LangGraph checkpointer is in-memory, so diagnosis state is lost on restart.
- Retrieval has only a small directional evaluation and has not been run against live Postgres with real runbooks.
- Single GPU inference server, no failover.

## 7. Running and testing

All commands are Makefile targets (`make help`):

- `make install`, `make dev-setup`: dependencies and environment.
- `make infra-up` / `infra-down`: Postgres+pgvector, Prometheus, Langfuse.
- `make api`: FastAPI on port 8010. `make demo` / `make demo-warning`: send a sample critical or non-critical alert.
- `make ingest-runbooks`: load sample runbooks.
- `make modal-deploy` / `modal-stop`: dev vLLM endpoints (stops GPU spend).
- `make test`, `make test-unit`, `make test-cov`, `make lint`, `make format`, `make typecheck`.
- `make judge-sanity`, `make judge-gate`, `make judge-gate-simulated`: LLM judge checks.
- `python -m evals.retrieval_ab`: retrieval A/B (RRF only vs three-way fusion vs reranker only).

Configuration is read from `.env` (see `.env.example`): `LLM_BASE_URL`, `EMBEDDING_BASE_URL`, `EGRESS_ALLOWED_HOSTS`, `AUTO_REMEDIATION_ENABLED`, `AUTO_ACTION_ALLOWLIST`, `AUTO_ACTION_TARGET_SERVICES`, `AUTO_ACTION_MAX_PER_HOUR`, the `POSTGRES_*` settings and the `RAG_*` settings (`AgentConfig` in `shared/config.py`).
