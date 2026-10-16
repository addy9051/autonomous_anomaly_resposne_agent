# Sandboxed SRE Intelligence Layer

> A **sandboxed intelligence layer for SREs**. Alerts trigger a multi-agent investigation that runs entirely inside your VPC against a self-hosted open-weight model. Non-critical events are auto-fixed through a small allowlist of reversible, policy-gated actions, or come with expert-recommended steps. Critical events get a root-cause analysis only, with no action. Built with **LangChain + LangGraph**, a **FastAPI** backend and a **Next.js** dashboard.

> **Deployment status:** AWS ECS/Fargate is the selected pilot target; the stack has not yet been provisioned. The GCP/GKE files under `infra/` are inactive and retained only for reference; CI/CD does not use them.

## Table of Contents

1. [How it works](#how-it-works)
2. [Components](#components)
3. [Connecting telemetry vendors](#connecting-telemetry-vendors-from-the-dashboard)
4. [Dashboard](#dashboard)
5. [Quick start](#quick-start)
6. [Project structure](#project-structure)
7. [API reference](#api-reference)
8. [Testing and evals](#testing-and-evals)
9. [Security](#security)
10. [CI/CD](#cicd)
11. [Known limitations](#known-limitations)

---

## How it works

```
Alert webhook ─► dedupe ─► episodic memory (Postgres): seen this before?
                              │ open/escalated duplicate → suppressed (logged on the original episode)
                              │ resolved+recovered duplicate → stored diagnosis replayed (policy gate still runs)
                              ▼ otherwise
                 Diagnosis graph (local LLM)
                   ├─ procedural memory: skills/<name>/SKILL.md selected by anomaly type, severity, service
                   ├─ specialist experts (database, network, security, application), each with its own RAG
                   │    collection + the shared `policy` collection (company policies, SLAs)
                   └─ evidence-cited hypotheses (confidence capped by real support)
                              ▼
        severity branch ── critical ─► RCA only, no action, escalated to the SRE
                        └─ other ────► allowlisted reversible action (policy-gated, verified) or recommended steps
                              ▼
                 SRE brief (issue, root cause + confidence, fix applied / recommended steps, evidence gaps,
                 duplicate-of, skills used) + per-stage timeline in episodic memory
```

1. **Intake.** `POST /api/v1/alerts/alertmanager` (and `/api/v1/events/process`) turns an alert into an `AnomalyEvent`, deduplicates it, and opens an incident. Auth is API Gateway IAM plus a trusted identity header.
2. **Memory check.** The fingerprint (sorted services + anomaly type + severity) is looked up in episodic memory within `DUPLICATE_WINDOW_MINUTES`. Duplicates are suppressed, replayed or re-diagnosed depending on how the prior episode ended. A failed prior fix always gets a fresh diagnosis.
3. **Evidence.** `agents/diagnosis/evidence.py` queries the telemetry adapter (Prometheus, Datadog, Splunk, or the simulator for tests) for up to 3 affected services over a 15-minute window, and adds recent GitHub deployments (`GITHUB_DEPLOY_REPO`). Each record carries event time, ingestion time, freshness, `is_simulated` and `source_ref`. A failing source is recorded as an **evidence gap**, never filled with mock data.
4. **Diagnosis.** LangGraph flow: `gather_context` → `rag_runbook_lookup` → `supervisor` (selects experts) → `synthesise_rca`. The model sees evidence with citable ids (E1…En) and the list of unavailable sources, and proposes 2-3 competing hypotheses. `agents/diagnosis/hypotheses.py` resolves citations against the real timeline (invented ids are dropped) and caps confidence (at most 0.4 with no real support, 0.7 with a single supporting record or any contradiction). If the output cannot be parsed, no action is proposed and the incident is escalated.
5. **Policy gate.** Critical incidents are always denied. Non-critical actions are auto-run only if allowlisted and reversible, `AUTO_REMEDIATION_ENABLED=true`, the service is in `AUTO_ACTION_TARGET_SERVICES`, and the hourly limit is not reached. Everything else needs SRE approval or is recommend-only. Execution goes through n8n workflows with an audit trail.
6. **Verification.** `agents/verification/verifier.py` re-reads metrics after the action using only authoritative records (simulated data is excluded). Outcomes are recovered, not recovered, or inconclusive; any adapter error yields inconclusive.
7. **Close-out.** PagerDuty and Slack updates, the incident store (hypotheses, queries, approvals), and the episodic-memory outcome plus SRE brief.

## Components

| Piece | Where | Notes |
|-------|-------|-------|
| Procedural memory | `skills/<name>/SKILL.md`, `shared/skills.py` | Agent-skills format: `name` and `description` front matter, with routing under `metadata` (`triggers`: anomaly_types, severities, services; `priority`; `version`). `baseline-conduct` always applies; `critical-rca-only` applies to critical. Edit files, no code change. |
| Per-agent RAG | `knowledge_base/`, `agents/diagnosis/experts.py` | `documents.collection` column. Ingest `<root>/<collection>/*.md` with `ingest_directory` (collections: `database`, `network`, `security`, `application`, `policy`). Hybrid vector + keyword search with reciprocal-rank fusion. Retrieval failure is recorded as a gap. |
| Episodic memory | `shared/episodic_memory.py`, `knowledge_base/migrations/003_episodic_memory.sql` | Postgres; falls back to in-process memory with a loud log (`episodic_memory_degraded` in `/api/v1/status`) if unreachable. |
| Action harness | `agents/action/policy.py`, `agents/action/tiers.py` | Deterministic policy; tiers: 1 reversible, 2 needs approval, 3 always human. The auto allowlist can only narrow the reversible set. |
| Egress harness | `shared/egress.py`, `infra/k8s/network-policy.yaml` | App-level fail-closed allowlist (private ranges + `EGRESS_ALLOWED_HOSTS` / `EGRESS_ALLOWED_CIDRS`) on the LLM client and every HTTP client, with a startup self-test. The NetworkPolicy is the network-level backstop. |
| Local models | `shared/llm.py` | OpenAI-compatible server in the VPC (`LLM_BASE_URL`; vLLM/Ollama). Embeddings via `EMBEDDING_*`. |
| Monitoring | `agents/monitoring/` | Anomaly detection for the telemetry stream path. |
| Verification | `agents/verification/` | Independent post-action recovery check. |
| Regression detector | `agents/feedback/regression.py` | Flags earlier fixes that did not hold. Also provides `alert_key` for dedupe. |

Inter-agent contracts are Pydantic v2 models in `shared/schemas.py`: `TelemetryEvent`, `AnomalyEvent`, `DiagnosisResult`, `ActionResult`, `IncidentRecord`.

**Setup for the sandbox:** serve a chat model and an embedding model inside the VPC and set `LLM_BASE_URL`; apply migration `003_episodic_memory.sql` (changing `EMBEDDING_DIMENSIONS` requires re-embedding); ingest runbooks, policies and SLAs per collection; apply `infra/k8s/network-policy.yaml`. SaaS vendors (Datadog, PagerDuty, Slack, GitHub) are blocked by default: route them privately or add them to `EGRESS_ALLOWED_HOSTS`.

## Connecting telemetry vendors from the dashboard

Open `/connectors` in the dashboard:

1. **Select** one or more connectors (Prometheus, Datadog, Splunk) as icon cards.
2. **Credentials**: fill in and "Test connection" for each. Saved secrets are never returned by the API (shown as "saved").
3. **Activate**: choose services, dry-run and poll interval. The selection builds `CONNECTOR_MODES` at runtime and starts combined live ingestion (`/api/v1/connectors*`, `/api/v1/ingestion/status`). Missing credentials do not crash startup; ingestion just stays inactive.

State is persisted to `.connector_state.json` (gitignored, **plaintext secrets**; use a secret manager in production) and overrides `CONNECTOR_MODES` from `.env`. Dashboard env: `NEXT_PUBLIC_API_BASE` (default `http://localhost:8050/api/v1`) and `NEXT_PUBLIC_API_KEY` (sent as `X-API-Key`). Default query templates are assumptions; override with `TELEMETRY_QUERY_TEMPLATES`. Not yet validated against live vendors.

## Dashboard

| Component | Data source |
|-----------|-------------|
| System pulse (telemetry scatter) | `GET /api/v1/telemetry/recent` |
| Active response workflows (incident lifecycle) | `GET /api/v1/incidents/active` |
| Status cards (active, resolved, episodic-memory health) | `GET /api/v1/status` |
| Connection status | `GET /api/v1/health/detailed` |
| Connector wizard | `/api/v1/connectors*` |

```bash
# Terminal 1: backend
poetry run uvicorn api:app --host 127.0.0.1 --port 8050 --reload

# Terminal 2: dashboard (http://localhost:3000)
cd dashboard
npm install
npm run dev
```

## Quick start

Prerequisites: Python 3.11+, Docker Desktop, Node.js 18+, and an OpenAI-compatible inference server (vLLM/Ollama) reachable from the app.

```bash
pip install poetry
poetry install
copy .env.example .env        # set LLM_BASE_URL and the EMBEDDING_* / EGRESS_* values
docker compose up -d          # or: docker compose -f docker-compose.lite.yml up -d (Redis + Postgres)
poetry run uvicorn api:app --host 127.0.0.1 --port 8050 --reload
poetry run python orchestrator.py --mode demo     # synthetic batch
poetry run pytest tests/ -v
```

## Project structure

```
├── agents/
│   ├── monitoring/          # anomaly detection + tools
│   ├── diagnosis/           # LangGraph supervisor-expert graph, per-expert RAG, hypotheses, evidence
│   ├── action/              # policy gate, tiers, workflows, PagerDuty, Slack
│   ├── verification/        # independent recovery check
│   └── feedback/            # regression detector (alert_key)
├── skills/                  # procedural memory (<name>/SKILL.md)
├── knowledge_base/          # ingestion, hybrid retrieval, migrations (pgvector, episodic memory)
├── shared/                  # config, schemas, llm, egress, skills, episodic_memory, incident_store, pii
├── data_pipeline/           # telemetry adapters, Alertmanager intake, deploy events, connector store
├── dashboard/               # Next.js SRE UI (incl. /connectors)
├── evals/                   # LLM-as-judge, agreement gate (local judge model)
├── tests/                   # unit, integration, evals
├── infra/                   # aws/terraform (active), k8s/network-policy.yaml, legacy GCP files
├── scripts/                 # chaos experiments, verification and seeding helpers
├── orchestrator.py          # severity-branched pipeline + CLI
├── api.py                   # FastAPI app
└── docs/                    # deployment and design documents
```

## API reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `GET` | `/api/v1/health/detailed` | Health with agent connectivity |
| `POST` | `/api/v1/alerts/alertmanager` | Alertmanager webhook intake |
| `POST` | `/api/v1/events/process` | Process a single telemetry event |
| `POST` | `/api/v1/demo/run` | Demo with synthetic events |
| `POST` | `/api/v1/stream/start` | Streaming simulation |
| `GET` | `/api/v1/telemetry/recent` | Last 100 telemetry events |
| `GET` | `/api/v1/incidents` | Recent incidents (status filter) |
| `GET` | `/api/v1/incidents/active` | Active incidents |
| `GET` | `/api/v1/incidents/{id}` | Incident details |
| `GET` | `/api/v1/incidents/{id}/brief` | SRE brief for an incident |
| `POST` | `/api/v1/incidents/{id}/approve` | Approve or reject a pending action |
| `GET` | `/api/v1/episodes/{id}` | Episodic-memory episode and timeline |
| `GET` | `/api/v1/status` | System status |
| `GET` | `/api/v1/actions/tiers` | Action tier reference |
| `POST` | `/api/v1/knowledge/seed` | Ingest sample runbooks |
| `GET` | `/api/v1/knowledge/search` | Hybrid RAG search |
| `GET` | `/api/v1/knowledge/health` | Knowledge base connectivity |
| `GET/POST` | `/api/v1/connectors*`, `/api/v1/ingestion/status` | Connector wizard and live ingestion |

## Testing and evals

```bash
poetry run pytest tests/ -v                 # everything
poetry run pytest tests/unit -v             # unit tests
poetry run ruff check .                     # lint
poetry run python scripts/run_chaos_experiments.py
python -m evals.run_judge_gate              # judge agreement gate, needs the in-VPC LLM server
```

The judge calls the local model (`JUDGE_MODEL`) and must come from a different provider than every agent model. `evals/provider_check.py` checks this deterministically from model names (unknown models fail; declare them with `MODEL_PROVIDER_MAP`). `local_judge_fn` refuses to run on a clash, and `run_judge_gate` prompts you to pick another provider (exit code 3 when non-interactive). Re-baseline agreement with human labels before trusting scores. The golden dataset test checks dataset integrity only; retrieval quality must be measured against the live knowledge base once it is ingested.

## Security

| Method | Environment | Details |
|--------|-------------|---------|
| `.env` file | Local development | Loaded via `pydantic-settings` |
| AWS Secrets Manager | ECS/Fargate pilot | ECS injects configured secret ARNs at startup |
| GitHub OIDC | CI deployment | Short-lived AWS role session, no stored access key |
| AWS IAM task role | ECS task | Scoped to the task's EFS access point |

The production-facing API uses SigV4 with API Gateway `AWS_IAM` authorization and a private VPC endpoint. See [AWS pilot deployment instructions](docs/DEPLOYMENT_SECURITY.md).

Network posture: egress is default-deny (see `shared/egress.py` and `infra/k8s/network-policy.yaml`). Keep tracing in-VPC (self-hosted Langfuse); do not enable hosted LangSmith. Optional integrations (Slack, PagerDuty, GitHub, n8n) are only reachable if explicitly allowlisted.

## CI/CD

GitHub Actions runs lint, tests and a Docker build on pull requests, and deploys to AWS ECS/Fargate on pushes to `main` using GitHub OIDC; pull requests receive no AWS credentials. Stages: `ruff` → `pytest` → build and push to ECR → force ECS deployment and wait for stability.

## Known limitations

| Area | Limitation |
|------|-----------|
| Validation | Unit-tested with mocks only: not yet run against a live local model, live Postgres or live vendors. |
| Open-weight models | Weaker at structured output than hosted frontier models; the hypothesis validation and "no action if unparseable" rule are the safety net. |
| Incident store | The incident, approval and audit store is still SQLite (`shared/incident_store.py`); only episodic memory is on Postgres. |
| Infra | `infra/k8s/deployment.yaml` still contains the old trainer/predictor deployments; Terraform has no VPC endpoints or egress rules for the LLM server yet. |
| Alert intake | Alertmanager cannot sign AWS IAM requests, so a gateway that injects the trusted identity header is required; diagnosis runs inside the webhook request; dedupe key is `service:anomaly_type`; anomaly type is a keyword guess; no PagerDuty intake. |
| Evidence window | Fixed 15-minute window, not anchored on the alert start; metrics and deploys only; the GitHub deploy repo is global, not per service. |
| Confidence | Hypothesis caps (0.4 / 0.7) are heuristics, not calibrated; the policy gate does not read hypothesis confidence. |
| Dashboard | Overview and connector pages work; Observability/KB/Security nav entries are placeholders. |
| Open audit items | Several GET endpoints in `api.py` are unauthenticated; `trigger_workflow` returns `mocked` without `N8N_API_KEY`; the Slack summary LLM call is unguarded; a stray debug `print` remains in `/api/v1/events/process`. |

## License

Private — Internal Use Only
