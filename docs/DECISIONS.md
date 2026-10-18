# Design decisions

Each entry records what we chose, what else we looked at, and the trade-offs we accept for our situation. The situation: a small team, a prototype to prove the idea, a laptop with a 4 GB GPU, a limited AWS account (new, with a GPU quota to raise), and a hard requirement that incident data and model traffic stay inside our own boundary.

Status key: **built** means it exists in code and is covered by tests; **ran** means it also ran end to end in a dev setup; **designed** means Terraform or docs only, never applied.

---

## 1. Product scope

### 1.1 A sandboxed intelligence layer, not an autonomous operator
- **Choice:** alerts trigger an investigation; critical incidents get root-cause analysis only; non-critical incidents get an allowlisted, reversible auto-fix or recommended steps. Status: **built**, critical path **ran**.
- **Alternatives:** full autonomous remediation for all severities (the original idea); advice-only for everything.
- **Trade-offs we accept:** less automation, since the SRE stays in the loop for anything that matters. In return we get a blast radius we can reason about. The original reinforcement-learning, CrewAI and fine-tuning parts were dropped because they added risk and complexity without proven value.

### 1.2 Severity-branched flow
- **Choice:** the severity decides the path in the orchestrator, and the policy gate enforces it again (critical is always denied) instead of trusting the model. Status: **built**.
- **Alternative:** let the LLM decide whether to act.
- **Trade-off:** rigid. Even a critical incident with an obvious fix is never auto-fixed. We prefer that.

---

## 2. Models and serving

### 2.1 Self-hosted open-weight models behind an OpenAI-compatible API
- **Choice:** vLLM serving open-weight models; the app only talks to `LLM_BASE_URL`. Status: **ran** (on Modal).
- **Alternatives:** hosted APIs (OpenAI, Anthropic); AWS Bedrock; Ollama.
- **Why not hosted or Bedrock:** incident data, runbooks and secrets would leave our boundary. We looked at Bedrock seriously (managed, private endpoints) and chose self-hosting for control and a simpler isolation story.
- **Why vLLM over Ollama:** it is the production serving path (batching, tool calling, an API key), and the user asked for it. Ollama would have run on the laptop GPU but is not what we deploy.
- **Trade-offs we accept:** we run and patch the GPU server ourselves; open models are weaker at structured output and tool use than frontier models; one server is a single point of failure. The "no action if output is unparseable" rule and hypothesis validation are the safety net.

### 2.2 Model choices
- **Choice:** production target Llama-3.1-70B-Instruct (AWQ INT4) or 8B for agents; `bge-base-en-v1.5` (768 dimensions) for embeddings. Dev on Modal: Qwen2.5-7B-Instruct for the agents, Llama-3.1-8B for the judge, bge-base for embeddings.
- **Alternatives:** `nomic-embed-text`, larger embedding models, a single model for everything.
- **Trade-offs:** 7-8B dev models give thin root causes (the first run produced a plausible "deployment regression" at confidence 0.4). Embedding size is tied to the pgvector column, so changing models means re-embedding everything.

### 2.3 Separate chat and embedding servers
- **Choice:** two vLLM processes, because one vLLM process serves one model. Status: **ran** on Modal, **designed** on AWS.
- **Alternative:** one server with a combined model, or a hosted embedding API.
- **Trade-off:** a second GPU process and more ports to secure.

### 2.4 The embedding call does not send `dimensions`
- **Choice:** removed the parameter. vLLM can reject it for non-Matryoshka models.
- **Trade-off:** the model's native size must equal `EMBEDDING_DIMENSIONS` and the pgvector column; mismatch is a startup-time problem, not a silent one.

---

## 3. Where it runs

### 3.1 Development: Modal
- **Alternatives:** local Ollama on the GTX 1650 (4 GB, only tiny models); local vLLM through WSL2 or Docker (Turing GPU, very small models, fiddly); a rented cloud GPU by the hour; AWS from day one.
- **Choice:** Modal functions running vLLM (`infra/modal/vllm_app.py`), because it gives real vLLM with a stronger model in about an hour and no quota wait. Status: **ran**.
- **Trade-offs we accept:** the endpoints are public (API-key protected), so the egress allowlist needs `*.modal.run` in dev only; cold starts take a minute or two and can return a confusing 303 or 404 first; GPUs bill while containers are up (`make modal-stop`). We did not confirm the amount of free credit.

### 3.2 Later-stage AWS migration: private VPC (not deployed)
- **Alternatives we compared:** Modal as production (public egress, less isolation); Bedrock; GCP/Kubernetes/Helm/ArgoCD (the repo's original infra); a private vLLM on a managed Kubernetes cluster; a laptop only.
- **Choice:** reserve the current Modal + local API/dashboard + local Postgres arrangement for controlled demos, and keep ECS/Fargate, GPU EC2, RDS Postgres, private subnets, VPC endpoints, SSM and private API Gateway as a possible later migration. Status: Modal path **ran**; AWS stack **designed, not applied**. The account used for exploration has zero G/VT quota, and required Terraform inputs are not yet available.
- **Rationale for keeping AWS as the later target:** its private-VPC approach offers a stronger isolation boundary for production/customer data than Modal's public inference endpoints.
- **Trade-offs we accept:**
  - Modal's inference endpoints are public HTTPS services protected by vLLM API keys. They are acceptable for synthetic-data demonstrations, not for production/customer telemetry.
  - The API and dashboard are not hosted on Modal; the current showcase runs them locally with local Postgres.
  - AWS was never applied, so real-world problems (AMI contents, vLLM flags, quota, image mirroring) are unknown.
  - Setup is heavy: a GPU quota request (can take a day), mirroring the vLLM image into ECR from an internet-connected machine, uploading weights to S3, running migrations through ECS Exec.
  - A single GPU instance; no failover.
  - A g5.12xlarge costs a few dollars an hour; we stop it when idle.
  - 70B AWQ across two A10G GPUs is tight; lower context or use a smaller model if it runs out of memory.
  - The Terraform and deployment guide need reconciliation with the now-Postgres incident/approval store before any plan is applied; EFS/SQLite assumptions are stale.

### 3.3 Cleanup of unused infra
- **Choice:** deleted the GCP Terraform, Kubernetes, Helm, ArgoCD, and the Grafana, OTel and Tempo configs; reduced docker-compose to Postgres+pgvector, Prometheus and self-hosted Langfuse.
- **Trade-off:** no multi-cloud path and no Kubernetes deployment. We can restore from git history if needed.

---

## 4. Isolation and safety

### 4.1 Egress guard in code plus the network
- **Choice:** a fail-closed allowlist in `shared/egress.py` (private ranges always allowed; public hosts need `EGRESS_ALLOWED_HOSTS`) on the LLM client and every HTTP client, with a startup self-test; the VPC with no internet route is the backstop. Status: **built**.
- **Alternative:** rely only on security groups.
- **Trade-off:** two layers to keep consistent. SaaS vendors (Datadog, PagerDuty, Slack, GitHub) are blocked until routed privately or allowlisted.

### 4.2 Deterministic action policy
- **Choice:** `agents/action/policy.py` with action tiers (1 reversible, 2 needs approval, 3 always human). The auto-run allowlist can only narrow the reversible set; critical is always denied. Status: **built**.
- **Alternative:** ask the LLM to judge safety.
- **Trade-off:** conservative and manual to extend, but it does not depend on a model behaving well. The judge test proved why: the 8B judge scored a `DROP TABLE` case 5/5.

### 4.3 Evidence-grounded hypotheses
- **Choice:** evidence gets citable ids; invented citations are dropped; confidence is capped (0.4 with no real support, 0.7 with one record or any contradiction). A failing source is recorded as an evidence gap, never filled with mock data. Status: **built**.
- **Trade-off:** the caps are heuristics, not calibrated, and the policy gate does not read confidence.

### 4.4 Network access to the model server
- **Choice:** an API key on every vLLM server (`VLLM_API_KEY`, stored in Secrets Manager on AWS) in addition to the private network, and offline Hugging Face mode on the AWS server.
- **Trade-off:** another secret to manage; defence in depth for a small cost.

### 4.5 Observability: self-hosted Langfuse only, hosted LangSmith blocked
- **Choice:** LLM/agent traces go to Langfuse (self-hosted in docker-compose, port 3001). Hosted LangSmith defaults to off, and `get_settings()` forces `LANGCHAIN_TRACING_V2` off and removes its API key from the environment unless its endpoint passes the egress check. `get_langfuse_handler()` in `shared/utils.py` returns `None` when keys are missing, the host fails the egress check, or the package is not installed; the agents import no Langfuse code at module level. Status: **built**, covered by `tests/unit/test_tracing_lockdown.py`.
- **Alternative:** hosted LangSmith or Langfuse Cloud (easiest, but they send prompts, evidence and responses outside the sandbox, which contradicts the air-gap goal).
- **Trade-off:** you run and maintain a Langfuse instance, and tracing is silently skipped (with no error) if it is misconfigured or unreachable. Logging is structlog.
- **Trace correlation:** the alert's `event_id` is the Langfuse `session_id` for all three agents (the action agent previously used `incident_id`, which is now in metadata) and is bound into every structlog line during processing. API requests get OpenTelemetry spans, and logs carry `trace_id`/`span_id`.
- **OpenTelemetry:** `setup_tracing(app)` runs at API startup and instruments FastAPI. Spans are exported only if `OTEL_EXPORTER_OTLP_ENDPOINT` is set and passes the egress check (use an in-VPC collector such as ADOT); it is empty by default, so spans stay in-process.
- **Langfuse 3 (upgrade):** the SDK is now 3.x, which no longer takes a session id on the handler. `langfuse_session_context(event_id)` in `shared/utils.py` wraps the monitoring step and `process_anomaly` in the orchestrator and sets the session through `propagate_attributes`. The 3.x SDK needs a 3.x server, so compose now runs `langfuse:3` and `langfuse-worker:3` with ClickHouse, Redis and MinIO (the 2.x server needed only Postgres). Verified: a trace sent through the upgraded code lands in the 3.x server under the right session, and a live alert produced grouped diagnosis and action traces.
- **Trade-off of that upgrade:** five containers instead of two for tracing, in exchange for a current SDK and the LangChain 1.x line. Compose secrets default to `dev_only_change_me`; set real values for anything shared.

### 4.6 LangChain 1.x
- **Choice:** the `langchain<1` cap, the exact `langgraph-checkpoint==3.0.1` pin and `langgraph-checkpoint-redis` are gone. Now: langchain, langchain-core, langchain-openai and langgraph on 1.x, langchain-community 0.4, Langfuse 3. The Redis checkpointer was already bypassed in `agents/diagnosis/graph.py`, so nothing used it. Status: **ran**; 310 tests pass and a live alert completed.
- **Alternative:** stay on 0.3, which kept `langchain-core` at 0.3 and blocked Langfuse 3.
- **Trade-off:** the lock file moved several packages at once; unused `langchain-anthropic` and `langchain-google-genai` may linger in an old venv until it is rebuilt from the lock. LangGraph state is not persisted across restarts (it never was).

---

## 5. Memory and knowledge

### 5.1 Skills as procedural memory (`skills/<name>/SKILL.md`)
- **Alternatives:** flat markdown files (the first version); prompts in code; fine-tuning.
- **Choice:** the SKILL.md agent-skills format, routed by metadata (anomaly type, severity, service), loaded by `shared/skills.py` (which still reads legacy flat files). Status: **built**.
- **Authoring decision:** the skills were authored with a Claude model (Sonnet) through a subagent, as requested. They were not reviewed by an SRE yet.
- **Trade-off:** skills are easy to edit but unverified content; they need an SRE review.

### 5.2 Postgres + pgvector for retrieval and episodic memory
- **Alternatives:** a Supabase cloud database (previous default); a separate vector store; Redis.
- **Choice:** one Postgres with pgvector. Hybrid vector plus keyword search with reciprocal-rank fusion; per-expert RAG collections (database, network, security, application, policy). Episodic memory records each investigation, so duplicates are suppressed or replayed. Status: **ran** (27 chunks ingested, critical episode stored).
- **Change made while testing:** `rag_dsn` used to prefer a configured Supabase URL; it now always uses the local Postgres, which fits the no-cloud-DB rule.
- **Trade-offs:** one database for both jobs; falls back to in-process memory with a loud log if Postgres is down. The incident, approval and audit store is now Postgres too (see 5.4).

### 5.3 Per-expert RAG
- **Choice:** specialist experts (database, network, security, application) each query their own collection plus the shared policy collection.
- **Trade-off:** more collections to curate; retrieval quality is unmeasured until real documents are ingested.

### 5.4 Incident, approval and audit store in Postgres
- **Alternatives:** keep SQLite on the task's disk (pins ECS to one task and loses state on redeploy); DynamoDB; reuse the old `incidents` table from `001_initial.sql`.
- **Choice:** `PostgresIncidentStore` in `shared/incident_store.py`, selected by `get_incident_store()` (default; `INCIDENT_STORE_BACKEND=sqlite` for the old file store). Tables `incident_records` (JSONB payload) and `incident_audit_trail`, created on start and in migration `004_incident_store.sql`. The approval claim locks the row (`SELECT ... FOR UPDATE`), so an approval stays single-use across several API tasks; the shared `_apply_approval` logic is the same for both backends. Status: **ran** (a live alert persisted and was restored after a restart); tested against a real Postgres.
- **Trade-offs:** the database is now required (no silent fallback to a local file, which would split state); synchronous psycopg2 calls block the event loop briefly, which is fine at pilot volume; a new table pair rather than the older, differently shaped `incidents` table, which is left unused.

---

## 6. Telemetry ingestion

### 6.1 Combined multi-vendor stream with a UI-driven connector wizard
- **Alternatives:** hardcoded `CONNECTOR_MODES` that crashes at startup without credentials; separate streams per vendor.
- **Choice:** the SRE picks Prometheus, Datadog and Splunk as icon cards in the dashboard, enters and tests credentials per connector, and activates; the selection builds `CONNECTOR_MODES` at runtime and starts one combined stream. Missing credentials leave ingestion inactive instead of crashing. Status: **built**; not validated against live vendors.
- **Trade-offs:** connector state is saved in a gitignored plaintext file; query templates are assumptions to override; use a secret manager in production.

### 6.2 Alertmanager intake
- **Choice:** `POST /api/v1/alerts/alertmanager` is the real entry point. Status: **ran**.
- **Trade-offs:** Alertmanager cannot sign AWS IAM requests, so a gateway that injects the trusted identity header is needed; diagnosis runs inside the webhook request; the dedupe key is `service:anomaly_type`.

---

## 7. LLM judge

### 7.1 Judge from a different provider than the agents, checked deterministically
- **Alternatives:** same model family as the agents; trust the config; a human-only review.
- **Choice:** `evals/provider_check.py` maps model names to a provider by pattern, fails closed on unknown names (`MODEL_PROVIDER_MAP` overrides), and the judge refuses to run on a clash. On a clash, the gate prompts the user to pick another provider, or exits with code 3 when non-interactive. Status: **built**, tested.
- **Trade-offs:** "provider" means model family or vendor, judged by the name, which is a heuristic. A renamed model could slip past it.

### 7.2 The judge has its own server
- **Choice:** `JUDGE_BASE_URL` and `JUDGE_API_KEY`; in dev a third Modal function serves Llama-3.1-8B (agents run Qwen). Status: **ran**.
- **Trade-off:** the AWS Terraform does not serve a judge yet; it needs another container or instance.

### 7.3 Judge validation: a sanity check now, human agreement later
- **Choice:** `make judge-sanity` runs the live judge over the 32 golden cases and checks valid output plus a separation of at least 1.0 between clean and flawed cases. Result: passed (valid 32/32, clean mean 4.28, flawed mean 2.86, separation 1.42). The real gate (`make judge-gate`) needs 30+ human labels and has none yet.
- **Known weakness:** it missed four flawed cases (tier understated, tier 3 executed, `DROP TABLE`, SSN in the root cause).
- **Simulated labels (not human):** to exercise the gate, a larger model of a third family (Mistral-Small-24B on Modal, Apache-2.0; `make simulate-labels`) rated all 32 cases three times each at temperature 0.7, reasoning first. Output is `evals/data/simulated_labels.jsonl`, every row marked `source: "ai_simulated"`, kept apart from `human_labels.jsonl`. `make judge-gate-simulated` result: **failed** (within-one 0.72, weighted kappa 0.52, unsafe-case recall 0.75 against thresholds 0.80, 0.60, 0.90). The labeller itself rated two deterministically unsafe cases (g03, g04) a 4, so these labels are noisy on safety and say little about humans.
- **Trade-off:** model-vs-model agreement is cheap but circular in places and is never evidence of human agreement. The real gate still needs 30+ human labels.
- **Trade-off:** a small judge is cheap and fast but not a safety control, so deterministic layers stay authoritative. The sanity check is not proof of agreement with humans.

---

## 8. Operations

### 8.1 Local development
- **Choice:** Docker Compose for Postgres and Prometheus; the API on port 8010 (something else on the machine used 8000); local auth through `REQUIRE_GATEWAY_AUTH=false` with `API_AUTH_TOKEN`; `make demo` fires a sample alert. The Makefile's stale `main.py` targets were removed.
- **Trade-off:** dev auth bypass exists only when `APP_ENV=development`; any other environment requires the gateway identity header.

### 8.2 Secrets
- **Choice:** Secrets Manager in AWS, `.env` locally.
- **Trade-off, and an open issue:** a dev `.env` held real OpenAI and Anthropic keys that this system no longer uses; they were exposed in a tool output and must be rotated and removed.

---

## 9. Accepted gaps (not decided against, just not done)

| Gap | Why we accept it for now |
|-----|--------------------------|
| Nothing applied to AWS | The prototype goal is to prove the pipeline. The exploratory account has zero G/VT quota; Terraform still needs real inputs (GPU AMI, model bucket, API-key secret ARN, GitHub OIDC provider) and the AWS design must be reconciled with the current Postgres store before planning/applying. |
| Single GPU instance, no failover | Cost and simplicity for a pilot. |
| No judge server in Terraform | The dev judge works; production needs a decision on a third GPU container. |
| Mocked n8n and an unguarded Slack LLM call | Known audit items, listed in the README. |
| Auto-fix path never run live | Executors are mocked, so it stays dry-run; the policy is covered by unit tests. The recommend path ran live (warning alert, tier 3, no auto-execution). |
| No live vendor validation (Prometheus, Datadog, Splunk) | Connector code is built, not exercised against real accounts. |
| Confidence caps are not calibrated | Heuristics until there is labelled data. |
| Skills not reviewed by an SRE | Content needs a domain review before production. |
