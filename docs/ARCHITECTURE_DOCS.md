# Technical Architecture & Implementation Audit

## 1. Executive Summary & Repository Map

### Executive Summary
The **Autonomous Anomaly Response Agent System** is an enterprise-grade 24/7 service reliability and automated incident response platform. Built with Python 3.11+, FastAPI, LangChain, LangGraph, CrewAI, Vowpal Wabbit, and Next.js, the system ingests infrastructure telemetry, detects anomalous operational patterns, performs multi-perspective root cause analysis (RCA), executes tiered remediation actions, and continuously learns via reinforcement learning (RL) contextual bandits and LLM-as-a-Judge feedback.

### Architecture Overview & Tech Stack
- **API & Orchestration:** FastAPI, Uvicorn, LangGraph (4-node state DAG with in-memory checkpointer), CrewAI (Specialist SRE sub-agents).
- **LLM Engine & Routing:** Multi-provider integration supporting OpenAI (`gpt-4o`, `gpt-4o-mini`), Google Vertex AI (`gemini-2.5-flash-lite`), and Anthropic (`claude-3-5-sonnet`). Includes automated regex-based PII sanitization.
- **RAG & Knowledge Base:** Hybrid retrieval using PostgreSQL (`pgvector` dense vector cosine search + BM25 trigram sparse search), Reciprocal Rank Fusion (RRF), and Cohere Cross-Encoder Reranking (`rerank-english-v3.0`).
- **Reinforcement Learning:** Vowpal Wabbit (`cb_explore_adf` contextual bandit) for online policy learning, combined with a hybrid reward model (60% extrinsic MTTR reduction + 40% intrinsic LLM-as-a-Judge semantic scoring).
- **Frontend & Control Center:** Next.js 15 (TypeScript, Tailwind CSS) dashboard and standalone HTML5/JS SRE Control Center.
- **Infrastructure & Observability:** Docker Compose, Kubernetes, Helm, ArgoCD, Terraform, Prometheus, OpenTelemetry, Grafana, and Tempo.

### Clean Repository Tree Structure
```
autonomous_anomaly_response_agent/
├── .env.example                          # Environment variable configuration template
├── Dockerfile                            # Production container definition
├── Makefile                              # Automation build and execution targets
├── README.md                             # Repository overview and setup guide
├── api.py                                # FastAPI REST server and endpoint definitions
├── orchestrator.py                       # Master Agent Orchestrator pipeline manager
├── pyproject.toml                        # Project dependencies and tool configurations
├── agents/                               # Multi-agent system implementations
│   ├── action/                           # Action Agent module (tiered remediation)
│   │   ├── agent.py                      # Action execution logic and Slack notifier
│   │   ├── pagerduty.py                  # PagerDuty REST API integration
│   │   ├── tiers.py                      # 3-tier action classification reference
│   │   └── workflows.py                  # N8n webhook client integration
│   ├── diagnosis/                        # Diagnosis Agent module (root cause analysis)
│   │   ├── crew.py                       # CrewAI multi-agent crew definitions
│   │   ├── experts.py                    # Specialized SRE expert domain models
│   │   ├── graph.py                      # LangGraph 4-node state DAG
│   │   └── prompts.py                    # System & synthesis prompt templates
│   ├── feedback/                         # Feedback Loop & RL module
│   │   ├── agent.py                      # Vowpal Wabbit contextual bandit agent
│   │   ├── finetuner.py                  # Negative reward fine-tuning dataset generator
│   │   ├── reward.py                     # Hybrid reward calculation engine
│   │   └── reward_agent.py               # LLM-as-a-Judge intrinsic evaluator
│   └── monitoring/                       # Monitoring Agent module (anomaly detection)
│       ├── agent.py                      # ReAct agent loop for telemetry scanning
│       ├── prompts.py                    # Monitoring system prompt templates
│       └── tools/                        # Infrastructure telemetry query tools
│           └── monitoring_tools.py       # Prometheus, Kafka, Redis, Isolation Forest tools
├── automation/                           # Operational automation scripts
│   └── scripts/
│       └── continuous_improvement.py     # Offline RL retraining exporter
├── data_pipeline/                        # Telemetry ingestion and feature processing
│   ├── connectors/
│   │   └── synthetic_producer.py         # Synthetic telemetry stream & anomaly generator
│   └── flink_jobs/
│       └── anomaly_features.py           # Sliding window metrics & alert deduplicator
├── knowledge_base/                       # Runbook RAG ingestion & hybrid search
│   ├── ingestion/
│   │   └── pipeline.py                   # Runbook document chunker & pgvector indexer
│   └── retrieval/
│       └── search.py                     # Hybrid vector + BM25 search & Cohere reranker
├── shared/                               # Cross-cutting utilities and domain schemas
│   ├── config.py                         # Pydantic Settings & K8s CSI secret manager
│   ├── llm.py                            # Multi-provider LLM factory router
│   ├── pii.py                            # Regex PII masking middleware
│   ├── pubsub.py                         # Google Cloud Pub/Sub wrapper
│   ├── schemas.py                        # Pydantic v2 domain schemas and data contracts
│   └── utils.py                          # Structlog, OpenTelemetry, Redis lock utilities
├── dashboard/                            # Next.js 15 SRE Dashboard Frontend
│   ├── app/                              # App router (layout.tsx, page.tsx)
│   ├── components/                       # Dashboard React components
│   └── lib/                              # Frontend API client library
├── ui/                                   # Standalone HTML5 SRE Control Center
│   ├── app.js                            # UI state management and API polling logic
│   ├── index.html                        # Dashboard HTML layout
│   └── style.css                         # Custom dark mode stylesheet
├── infra/                                # Infrastructure as Code & Deployment
│   ├── argocd/                           # ArgoCD GitOps applications
│   ├── docker/                           # Container build configurations
│   ├── helm_charts/                      # Kubernetes Helm charts
│   ├── k8s/                              # Raw Kubernetes manifests
│   └── terraform/                        # GCP Cloud Infrastructure IaC
├── observability/                        # Observability Stack Configurations
│   ├── grafana/                          # Dashboards & provisioning
│   ├── otel/                             # OpenTelemetry collector config
│   ├── prometheus/                       # Prometheus rules & alertmanager
│   └── tempo/                            # Grafana Tempo tracing config
└── scripts/                              # Verification and chaos test scripts
    ├── run_chaos_experiments.py          # Synthetic chaos injection simulator
    ├── seed_knowledge_base.py            # RAG knowledge base seeder
    └── verify_*.py                       # Component validation scripts
```

---

## 2. Detailed Module Breakdown

### 2.1 API & Orchestration Module
- **Purpose:** Exposes REST API endpoints for telemetry submission, incident status tracking, human approval workflows, and runbook management. Manages the lifecycle of telemetry events through the multi-agent pipeline.
- **File Components:**
  - `api.py`
  - `orchestrator.py`
- **Data Flow & Dependencies:**
  - Data enters via FastAPI POST endpoints (`/api/v1/events/process`, `/api/v1/demo/run`, `/api/v1/stream/start`).
  - Calls `AgentOrchestrator` to route telemetry through Monitoring -> Alert Deduplication -> Diagnosis -> Action Execution -> Semantic Evaluation -> Feedback Learning.
  - Dependencies: FastAPI, Pydantic, asyncio, Uvicorn.
- **Component Status Table:**

| Component/Function/Route | Type | Status | Notes / Missing Logic |
| --- | --- | --- | --- |
| `lifespan` / `pulse_heartbeat` | Async Context | [IMPLEMENTED] | Runs background heartbeat pulse generating synthetic metric events every 2s to populate dashboard charts. |
| `GET /health` | API Endpoint | [IMPLEMENTED] | Returns active/resolved incident counts and component health status. |
| `GET /api/v1/health/detailed` | API Endpoint | [IMPLEMENTED] | Diagnostic breakdown for UI connection verification. |
| `POST /api/v1/events/process` | API Endpoint | [IMPLEMENTED] | Processes a single inbound telemetry event through the orchestrator pipeline. |
| `POST /api/v1/demo/run` | API Endpoint | [IMPLEMENTED] | Generates a batch of synthetic events and runs incident detection. |
| `POST /api/v1/stream/start` | API Endpoint | [IMPLEMENTED] | Runs continuous synthetic telemetry streaming. |
| `GET /api/v1/telemetry/recent` | API Endpoint | [IMPLEMENTED] | Returns last 100 buffered telemetry events for dashboard pulsing. |
| `GET /api/v1/incidents/active` | API Endpoint | [IMPLEMENTED] | Lists active incidents currently being diagnosed or executed. |
| `GET /api/v1/incidents/{incident_id}` | API Endpoint | [IMPLEMENTED] | Fetches full incident record by ID. |
| `POST /api/v1/incidents/{id}/approve` | API Endpoint | [IMPLEMENTED] | Approves pending Tier 2 actions and transitions incident status to RESOLVED. |
| `GET /api/v1/status` | API Endpoint | [IMPLEMENTED] | Aggregates orchestrator metrics and RL policy status. |
| `GET /api/v1/feedback/policy` | API Endpoint | [IMPLEMENTED] | Returns current contextual bandit policy statistics. |
| `GET /api/v1/actions/tiers` | API Endpoint | [IMPLEMENTED] | Returns tier classification reference mapping. |
| `GET /api/v1/feedback/rewards` | API Endpoint | [IMPLEMENTED] | Fetches history of RL rewards for performance visualization. |
| `POST /api/v1/knowledge/seed` | API Endpoint | [IMPLEMENTED] | Triggers runbook ingestion pipeline into pgvector. |
| `GET /api/v1/knowledge/search` | API Endpoint | [IMPLEMENTED] | Executes hybrid RAG search query. |
| `AgentOrchestrator.process_event` | Method | [IMPLEMENTED] | Full pipeline coordinator handling telemetry buffering, feature extraction, anomaly filtering, RCA graph invocation, action execution, semantic judging, and RL feedback logging. |

---

### 2.2 Monitoring Agent Module
- **Purpose:** Acts as the first line of defense. Processes inbound telemetry through a LangChain ReAct reasoning loop with tool access to detect infrastructure anomalies and calculate confidence.
- **File Components:**
  - `agents/monitoring/agent.py`
  - `agents/monitoring/prompts.py`
  - `agents/monitoring/tools/monitoring_tools.py`
- **Data Flow & Dependencies:**
  - Accepts `TelemetryEvent` objects, sanitizes PII, invokes bound LangChain tools (`prometheus_query`, `kafka_lag_inspector`, `fraud_signal_fetch`, `baseline_compare`, `anomaly_classifier`), and produces an `AnomalyEvent` when confidence exceeds threshold (default 0.75).
  - Dependencies: LangChain Core, Langfuse, httpx, numpy, scikit-learn.
- **Component Status Table:**

| Component/Function/Route | Type | Status | Notes / Missing Logic |
| --- | --- | --- | --- |
| `MonitoringAgent.process_event` | Method | [IMPLEMENTED] | Traces via OpenTelemetry and Langfuse; executes ReAct loop and parses output into `AnomalyEvent`. |
| `prometheus_query` | Tool | [SCAFFOLDING] | Queries HTTP Prometheus API. **Missing/Scaffolding Logic:** When connection fails (`ConnectError`), falls back to `_synthetic_prometheus_response()` generating random synthetic numpy metrics instead of raising or retrying against secondary endpoint. |
| `kafka_lag_inspector` | Tool | [SCAFFOLDING] | **Missing/Scaffolding Logic:** Does not connect to a real Kafka cluster or Kafka AdminClient; uses `np.random.default_rng()` to generate synthetic partition lag data. |
| `fraud_signal_fetch` | Tool | [SCAFFOLDING] | **Missing/Scaffolding Logic:** Does not query Redis Feature Store; generates synthetic beta-distribution fraud scores in memory. |
| `baseline_compare` | Tool | [IMPLEMENTED] | Computes exact Z-scores and percentage deviation against rolling baselines. |
| `anomaly_classifier` | Tool | [IMPLEMENTED] | Trains an in-memory `IsolationForest` model on synthetic baseline vectors and scores input metrics. |

---

### 2.3 Diagnosis Agent Module
- **Purpose:** Executes root cause analysis (RCA) on detected anomalies using a 4-node LangGraph Directed Acyclic Graph (DAG) and specialized SRE expert sub-agents.
- **File Components:**
  - `agents/diagnosis/graph.py`
  - `agents/diagnosis/prompts.py`
  - `agents/diagnosis/experts.py`
  - `agents/diagnosis/crew.py`
- **Data Flow & Dependencies:**
  - Graph flow: `gather_context` -> `rag_runbook_lookup` -> `supervisor_node` -> `synthesise_rca`.
  - Context is gathered, hybrid RAG searches pgvector, supervisor triages domain and invokes `DatabaseExpert`, `NetworkExpert`, `SecurityExpert`, or `ApplicationExpert` in parallel via `asyncio.gather`, synthesizing findings into a `DiagnosisResult`.
  - Dependencies: LangGraph, LangChain, CrewAI, asyncpg.
- **Component Status Table:**

| Component/Function/Route | Type | Status | Notes / Missing Logic |
| --- | --- | --- | --- |
| `gather_context` | Node | [IMPLEMENTED] | Pulls anomaly payload, sanitizes PII, and generates operational context summary using LLM. |
| `rag_runbook_lookup` | Node | [SCAFFOLDING] | Searches hybrid pgvector knowledge base. **Missing/Scaffolding Logic:** Falls back to hardcoded `_get_synthetic_runbooks()` dictionary when PostgreSQL `pgvector` database is unreachable or empty. |
| `supervisor_node` | Node | [IMPLEMENTED] | Triages incident and dispatches specialized expert agents in parallel. Note: Uses `MemorySaver` checkpointer instead of Redis due to local pickle serialization constraints. |
| `synthesise_rca` | Node | [IMPLEMENTED] | Aggregates all evidence and sub-agent reports into a structured `DiagnosisResult` schema. |
| `DatabaseExpert.investigate` | Method | [IMPLEMENTED] | Analyzes queries, locks, connection pools, and replication lag via LLM. |
| `NetworkExpert.investigate` | Method | [IMPLEMENTED] | Analyzes DNS, CDN, BGP, firewalls, and load balancing via LLM. |
| `SecurityExpert.investigate` | Method | [IMPLEMENTED] | Analyzes authentication, WAF, scraping, and token anomalies via LLM. |
| `ApplicationExpert.investigate` | Method | [IMPLEMENTED] | Analyzes Kubernetes pods, deployments, memory leaks, and thread pools via LLM. |
| `create_diagnosis_crew` / `run_diagnosis_crew` | Function | [IMPLEMENTED] | Backup multi-agent crew execution using CrewAI framework. |

---

### 2.4 Action Agent Module
- **Purpose:** Executes remediation actions recommended by the Diagnosis Agent based on a 3-tier safety classification model (Tier 1 Autonomous, Tier 2 SRE Approval Required, Tier 3 Human Escalation Only).
- **File Components:**
  - `agents/action/agent.py`
  - `agents/action/tiers.py`
  - `agents/action/workflows.py`
  - `agents/action/pagerduty.py`
- **Data Flow & Dependencies:**
  - Takes `DiagnosisResult`, classifies actions via `classify_action()`. Triggers N8n webhooks for Tier 1, requests Slack approval for Tier 2, and triggers PagerDuty incidents for Tier 3. Sends Slack summary notification.
  - Dependencies: Slack SDK, httpx, Pydantic.
- **Component Status Table:**

| Component/Function/Route | Type | Status | Notes / Missing Logic |
| --- | --- | --- | --- |
| `ActionAgent.execute` | Method | [IMPLEMENTED] | Main executor managing action loops, summary generation, Slack notifications, and incident status updates. |
| `_execute_tier1` | Method | [IMPLEMENTED] | Executes low blast-radius actions autonomously via workflow triggers. |
| `_execute_tier2` | Method | [SCAFFOLDING] | **Missing/Scaffolding Logic:** In development mode (`app_env == "development"`), automatically approves and executes Tier 2 actions immediately without waiting for interactive Slack webhook confirmation. |
| `_execute_tier3` | Method | [IMPLEMENTED] | Escalates high blast-radius actions to on-call engineers via PagerDuty API. |
| `classify_action` | Function | [IMPLEMENTED] | Classifies actions into Tier 1, Tier 2, or Tier 3 using defined registry dict. |
| `trigger_workflow` | Function | [SCAFFOLDING] | Triggers N8n automation webhooks. **Missing/Scaffolding Logic:** Returns simulated/mocked success response when `n8n_api_key` is unconfigured or N8n server returns `ConnectError`. |
| `trigger_pagerduty_incident` | Method | [IMPLEMENTED] | Creates PagerDuty incident via REST API v2 (simulates gracefully if API key missing). |
| `resolve_pagerduty_incident` | Function | [SCAFFOLDING] | **Missing/Scaffolding Logic:** Logs resolution intention but does not execute HTTP request to PagerDuty REST or Events API to resolve incidents. |

---

### 2.5 Feedback Loop & Reinforcement Learning Module
- **Purpose:** Implements online reinforcement learning via a Contextual Bandit (Vowpal Wabbit) to optimize action selection over time based on incident outcomes and LLM-as-a-Judge semantic quality feedback.
- **File Components:**
  - `agents/feedback/agent.py`
  - `agents/feedback/reward.py`
  - `agents/feedback/reward_agent.py`
  - `agents/feedback/finetuner.py`
- **Data Flow & Dependencies:**
  - Resolves incident -> `RewardAgent` calculates qualitative intrinsic scores -> `compute_reward()` computes hybrid score -> `FeedbackLoopAgent.record_outcome()` trains Vowpal Wabbit model online with A/B splitting (control vs. experimental).
  - Dependencies: `vowpalwabbit` (`pyvw`), Google Cloud Storage SDK, Redis, NumPy.
- **Component Status Table:**

| Component/Function/Route | Type | Status | Notes / Missing Logic |
| --- | --- | --- | --- |
| `FeedbackLoopAgent.__init__` | Constructor | [IMPLEMENTED] | Initializes Vowpal Wabbit `--cb_explore_adf` model instance and A/B test experimental instance. |
| `FeedbackLoopAgent.record_outcome` | Method | [IMPLEMENTED] | Converts incident context to VW format, trains model online, buffers experience, and syncs policy. |
| `FeedbackLoopAgent.suggest_action` | Method | [IMPLEMENTED] | Predicts action probability distribution for inbound incidents. |
| `sync_model_to_gcs` / `from_gcs` | Method | [IMPLEMENTED] | Uploads/downloads binary VW model files to/from Google Cloud Storage buckets. |
| `compute_reward` | Function | [IMPLEMENTED] | Calculates weighted hybrid reward (60% extrinsic MTTR + 40% intrinsic LLM score). |
| `RewardAgent.evaluate` | Method | [IMPLEMENTED] | LLM-as-a-Judge scoring logical consistency, action relevance, and expert accuracy. |
| `generate_finetuning_dataset` | Function | [IMPLEMENTED] | Extracts negative reward / human-overridden incidents into OpenAI JSONL fine-tuning format. |

---

### 2.6 Data Pipeline & Feature Extraction Module
- **Purpose:** Generates synthetic telemetry streams and extracts real-time time-series features (sliding window averages, percentiles, z-scores) and alert deduplication.
- **File Components:**
  - `data_pipeline/connectors/synthetic_producer.py`
  - `data_pipeline/flink_jobs/anomaly_features.py`
- **Data Flow & Dependencies:**
  - `SyntheticTelemetryProducer` yields normal/anomalous `TelemetryEvent`s -> `RollingWindowAggregator` computes window statistics -> `AlertDeduplicator` suppresses duplicate alerts within 30-second tumbling windows.
  - Dependencies: NumPy, Python standard library.
- **Component Status Table:**

| Component/Function/Route | Type | Status | Notes / Missing Logic |
| --- | --- | --- | --- |
| `SyntheticTelemetryProducer` | Class | [IMPLEMENTED] | Generates realistic normal transactions, infrastructure metrics, and injected anomalies (latency spikes, error rates, fraud signals, resource saturation, volume anomalies). |
| `RollingWindowAggregator` | Class | [SCAFFOLDING] | **Missing/Scaffolding Logic:** Implemented as a Python in-memory sliding window class rather than an Apache Flink streaming job (PyFlink code is not deployed/integrated). |
| `AlertDeduplicator` | Class | [IMPLEMENTED] | Implements tumbling window alert deduplication and suppression counting. |

---

### 2.7 Knowledge Base & Hybrid RAG Module
- **Purpose:** Ingests markdown runbooks into PostgreSQL `pgvector` and performs hybrid dense/sparse search with reciprocal rank fusion and Cohere cross-encoder reranking.
- **File Components:**
  - `knowledge_base/ingestion/pipeline.py`
  - `knowledge_base/retrieval/search.py`
- **Data Flow & Dependencies:**
  - Markdown text -> `RecursiveCharacterTextSplitter` (512 token chunk / 64 overlap) -> `OpenAIEmbeddings` (`text-embedding-3-small`, 768 dims) -> PostgreSQL `pgvector`.
  - Retrieval query -> Dense cosine search + Sparse BM25 search -> RRF fusion -> Cohere Rerank API (`rerank-english-v3.0`) -> `RunbookReference` list.
  - Dependencies: LangChain, OpenAI, Cohere, asyncpg, pgvector.
- **Component Status Table:**

| Component/Function/Route | Type | Status | Notes / Missing Logic |
| --- | --- | --- | --- |
| `RunbookIngestionPipeline.ingest_document` | Method | [IMPLEMENTED] | Chunks runbooks, generates embeddings, and performs upsert into PostgreSQL `documents` table. |
| `RunbookIngestionPipeline.ingest_sample_runbooks` | Method | [IMPLEMENTED] | Ingests comprehensive default SRE runbooks (latency, DB pool, fraud, K8s crash loop, security). |
| `HybridSearchService.search` | Method | [IMPLEMENTED] | Executes vector + BM25 search, RRF fusion, and Cohere cross-encoder reranking. |
| `HybridSearchService.healthcheck` | Method | [IMPLEMENTED] | Validates database connectivity and document counts. |

---

### 2.8 Shared Infrastructure & Utilities Module
- **Purpose:** Centralized settings validation, multi-provider LLM routing, PII sanitization, structured logging, distributed locking, and cost tracking.
- **File Components:**
  - `shared/config.py`
  - `shared/llm.py`
  - `shared/pii.py`
  - `shared/pubsub.py`
  - `shared/schemas.py`
  - `shared/utils.py`
- **Data Flow & Dependencies:**
  - Used cross-cuttingly across all modules. PII masking sanitizes all string inputs before LLM invocations.
  - Dependencies: Pydantic Settings, Structlog, OpenTelemetry, Redis, SQLAlchemy.
- **Component Status Table:**

| Component/Function/Route | Type | Status | Notes / Missing Logic |
| --- | --- | --- | --- |
| `Settings` / `get_settings()` | Class / Function | [IMPLEMENTED] | Centralized Pydantic settings loading from `.env` and `/mnt/secrets` Kubernetes CSI driver mounts. |
| `get_chat_model` | Function | [IMPLEMENTED] | Router dispatching model creation to OpenAI, Anthropic Claude, or Google Gemini. |
| `sanitize_for_llm` / `sanitize_dict` | Function | [IMPLEMENTED] | Regex sanitization of card numbers, SSNs, emails, IPs, phone numbers, merchant IDs, and API keys. |
| `PubSubClient` | Class | [SCAFFOLDING] | GCP Pub/Sub client. **Missing/Scaffolding Logic:** When `google-cloud-pubsub` library is missing or `pubsub_project_id` is empty, falls back to returning mock message IDs (`mock-message-id`). |
| `LLMCostTracker` | Class | [IMPLEMENTED] | Enforces per-incident token limits (50K tokens) and calculates USD costs. |
| `acquire_distributed_lock` | Function | [IMPLEMENTED] | Redis SETNX distributed locking mechanism. |

---

### 2.9 Operational Automation & Scripts Module
- **Purpose:** Automated scripts for offline retraining export, chaos testing, knowledge base seeding, and component verification.
- **File Components:**
  - `automation/scripts/continuous_improvement.py`
  - `scripts/run_chaos_experiments.py`
  - `scripts/seed_knowledge_base.py`
  - `scripts/verify_*.py`
- **Component Status Table:**

| Component/Function/Route | Type | Status | Notes / Missing Logic |
| --- | --- | --- | --- |
| `fetch_overrides_for_retraining` | Function | [SCAFFOLDING] | **Missing/Scaffolding Logic:** Uses a hardcoded mock data dictionary instead of querying real Langfuse SDK traces. |
| `run_chaos_experiments.py` | Script | [IMPLEMENTED] | Chaos simulation injecting synthetic latency spikes and verifying automated mitigation. |
| `seed_knowledge_base.py` | Script | [IMPLEMENTED] | Command-line script to populate pgvector with runbooks. |
| `verify_*.py` | Scripts | [IMPLEMENTED] | Verification suite testing Cohere, Pub/Sub, Slack, RAG, RL learning, and specialized agents. |

---

### 2.10 Frontend & Dashboard Module
- **Purpose:** Provides user-facing real-time operational monitoring, active incident review, manual Tier 2 action approval, and policy performance analytics.
- **File Components:**
  - `ui/index.html`, `ui/app.js`, `ui/style.css` (Standalone SRE Control Center)
  - `dashboard/` (Next.js 15 TypeScript Dashboard)
- **Component Status Table:**

| Component/Function/Route | Type | Status | Notes / Missing Logic |
| --- | --- | --- | --- |
| Standalone SRE Control Center | UI Page | [IMPLEMENTED] | HTML5/JS dark-mode dashboard mounted at `/ui/` with real-time polling, telemetry injection, and action approval. |
| Next.js App (`dashboard/`) | Web App | [IMPLEMENTED] | Next.js 15 TypeScript application featuring `TelemetryPulse`, `ActiveIncidents`, `StatusCards`, and `PolicyPerformance` components. |

---

## 3. The Gap Analysis (What is Missing)

This section consolidates all identified `[SCAFFOLDING]`, mock implementations, and incomplete integration logic into a prioritized technical debt and readiness checklist.

### High Priority (Critical Infrastructure & Real Integration Gaps)

- [ ] **Kafka Infrastructure Integration (`agents/monitoring/tools/monitoring_tools.py`):**
  - *Current State:* `kafka_lag_inspector` generates synthetic partition lag data using NumPy random distributions.
  - *Missing Logic:* Replace mock with real `confluent-kafka` or `kafka-python` `AdminClient` queries to measure consumer group lag against live Kafka brokers.
- [ ] **Redis Feature Store Connector (`agents/monitoring/tools/monitoring_tools.py`):**
  - *Current State:* `fraud_signal_fetch` returns hardcoded beta-distribution metrics.
  - *Missing Logic:* Connect tool to production Redis Feature Store instance to pull real-time fraud model inference outputs and feature tensors.
- [ ] **PagerDuty Resolution Execution (`agents/action/pagerduty.py`):**
  - *Current State:* `resolve_pagerduty_incident` logs the resolution event but does not issue HTTP requests.
  - *Missing Logic:* Implement PagerDuty Events API v2 / REST API payload to resolve incidents automatically when the agent mitigates an issue.
- [ ] **Tier 2 Action Approval Workflow in Dev Mode (`agents/action/agent.py`):**
  - *Current State:* `_execute_tier2` automatically approves Tier 2 actions in development mode (`app_env == "development"`).
  - *Missing Logic:* Implement interactive Slack Block Kit webhook listener in FastAPI to require explicit human SRE button clicks before proceeding.

### Medium Priority (Production Readiness & Scalability)

- [ ] **Apache Flink Streaming Integration (`data_pipeline/flink_jobs/anomaly_features.py`):**
  - *Current State:* `RollingWindowAggregator` is implemented as an in-memory Python class.
  - *Missing Logic:* Deploy a true PyFlink streaming job on an Apache Flink cluster for distributed stateful window aggregation.
- [ ] **Prometheus Client Fallback Strategy (`agents/monitoring/tools/monitoring_tools.py`):**
  - *Current State:* `prometheus_query` falls back to `_synthetic_prometheus_response()` on connection error.
  - *Missing Logic:* Implement multi-Prometheus failover targets and exponential backoff retry rather than silent synthetic metric substitution.
- [ ] **N8n Automation Integration (`agents/action/workflows.py`):**
  - *Current State:* `trigger_workflow` returns simulated success when `N8N_API_KEY` is not present or N8n returns connection error.
  - *Missing Logic:* Ensure production deployments enforce strict N8n API authentication and fallback execution handlers.
- [ ] **Google Cloud Pub/Sub Client Fallback (`shared/pubsub.py`):**
  - *Current State:* `PubSubClient` returns mock message IDs when GCP library is missing or project ID is empty.
  - *Missing Logic:* Add strict environment validation in production mode to prevent silent message dropping.

### Low Priority (Operational Automation & Analytics)

- [ ] **Offline RL Training Exporter (`automation/scripts/continuous_improvement.py`):**
  - *Current State:* `fetch_overrides_for_retraining` exports a hardcoded sample Pandas DataFrame.
  - *Missing Logic:* Connect script directly to Langfuse API / PostgreSQL incident history to query real human override records (`human_overrode == True`).
- [ ] **RAG Database Fallback (`agents/diagnosis/graph.py`):**
  - *Current State:* `rag_runbook_lookup` falls back to hardcoded `_get_synthetic_runbooks()` dictionary when pgvector is unavailable.
  - *Missing Logic:* Ensure pgvector database health is guaranteed via Kubernetes readiness probes and persistent storage.

---

## 4. Execution & Environment Notes

### Detected Environmental Requirements
- **Python Version:** Python >= 3.11
- **Node.js Version:** Node.js >= 18.0.0 (for Next.js dashboard)
- **Primary Database:** PostgreSQL 16+ with `pgvector` extension enabled (or Supabase Cloud)
- **State Store / Cache:** Redis 7+
- **External API Keys (Optional / Env Dependent):**
  - `OPENAI_API_KEY`: Required for default LLM routing (`gpt-4o`, `gpt-4o-mini`) and embeddings (`text-embedding-3-small`).
  - `COHERE_API_KEY`: Optional; enables cross-encoder reranking in RAG (`rerank-english-v3.0`).
  - `SLACK_BOT_TOKEN`: Optional; enables real Slack incident alerts and interactive approval blocks.
  - `PAGERDUTY_API_KEY`: Optional; enables PagerDuty Tier 3 incident creation.
  - `N8N_API_KEY`: Optional; enables automated workflow execution via N8n webhooks.

### Key Build & Execution Commands (via `Makefile`)

#### Development Setup & Dependencies
```bash
# Install Python dependencies via Poetry
poetry install

# Run database migrations / pgvector setup
poetry run python -m knowledge_base.migrations.init_db

# Seed knowledge base with default runbooks
poetry run python scripts/seed_knowledge_base.py
```

#### Running Application Services
```bash
# Start FastAPI backend server (Port 8000)
poetry run uvicorn api:app --host 0.0.0.0 --port 8000 --reload

# Run synthetic telemetry demo CLI
poetry run python orchestrator.py --mode demo

# Run synthetic telemetry streaming CLI
poetry run python orchestrator.py --mode stream --eps 2.0 --duration 60

# Start Next.js Frontend Dashboard (Port 3000)
cd dashboard && npm run dev
```

#### Running Tests & Verification
```bash
# Run unit tests via pytest
poetry run pytest tests/unit

# Run integration tests
poetry run pytest tests/integration

# Run chaos experiment simulator
poetry run python scripts/run_chaos_experiments.py
```

#### Docker & Infrastructure
```bash
# Start full local stack via Docker Compose (Postgres, Redis, Prometheus, Grafana, API)
docker compose up -d

# Start lite stack (API + UI only)
docker compose -f docker-compose.lite.yml up -d
```
