# 24/7 Service Reliability & Anomaly-Response Agent System

> End-to-end multi-agent AI system monitoring payment transaction flows at scale using **LangChain + LangGraph + CrewAI**.

![Python](https://img.shields.io/badge/Python-3.11+-blue)
![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-green)
![CrewAI](https://img.shields.io/badge/CrewAI-0.80+-orange)
![Docker](https://img.shields.io/badge/Docker-Compose-blue)
![License](https://img.shields.io/badge/License-Private-red)

---

## 🏗️ Architecture

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│   🔭 Monitoring  │ ──▶  │   🔬 Diagnosis   │ ──▶  │   ⚙️ Action     │ ──▶  │   🔄 Feedback    │
│     Agent        │      │     Agent        │      │     Agent       │      │   Loop Agent     │
│                  │      │                  │      │                 │      │                  │
│ LangChain ReAct  │      │ LangGraph DAG    │      │ N8n Workflows   │      │ Contextual       │
│ Prometheus Tools │      │ CrewAI Sub-Agents│      │ Slack Approvals │      │ Bandit RL        │
│ Isolation Forest │      │ RAG (pgvector)   │      │ 3-Tier Actions  │      │ Reward Shaping   │
└─────────────────┘      └─────────────────┘      └─────────────────┘      └─────────────────┘
```

### Agents

| Agent | Framework | Purpose | Key Features |
|-------|-----------|---------|--------------|
| **Monitoring** | LangChain + ReAct | Anomaly detection from telemetry | Isolation Forest, z-score, 5 custom tools |
| **Diagnosis** | LangGraph + CrewAI | Root cause analysis | 4-node DAG, 3 sub-agents, RAG runbook search |
| **Action** | N8n + Slack | Tiered remediation | Tier 1 auto, Tier 2 approval, Tier 3 human |
| **Feedback** | Contextual Bandit | Continuous improvement | Reward shaping, policy retraining, A/B testing |

---

## 🧠 Architectural Evolution (Lean & Scalable MVP)

Based on the original design specifications, several strategic architectural pivots were made to heavily optimize for cost-reduction, maintainability, and data-privacy while preserving the entire cognitive agentic loop:

1. **Self-Hosted Privacy vs SaaS**: Instead of routing sensitive telemetry out to Datadog LLM Observability, this system leverages self-hosted **Langfuse** and **Arize Phoenix** (via `.docker-compose.yml`). This guarantees zero PII escapes the network perimeter—a crucial compliance feature for FinTech.
2. **Local-First RAG**: High-cost Vertex AI Matching Engine instances were bypassed. The Knowledge Base uses **pgvector** running directly alongside the agents, removing the cloud vector database overhead while providing identical cosine-similarity performance.
3. **API-First Stream Processing**: Heavy distributed stream processors (Apache Flink clusters) and feature stores (Feast) were stripped out in favor of handling Kafka streams via lightweight Python orchestration. This enables deployment to serverless containers without massive JVM overheads.
4. **Action Engine Abstraction**: To avoid third-party subscription traps, the N8n Action execution engine is gracefully mocked via Python dry-runs. The 3-tier classification logic remains fully intact, meaning N8n webhooks can be cleanly connected if budget allows, without altering the Action Agent prompt schema.
5. **Consolidated Serverless Footprint**: Instead of fragmented deployments across GKE and Cloud Run, the system consolidates onto **GKE Autopilot**, benefiting from reliable Kubernetes native networking and secrets management while still automatically scaling resources (and costs) to zero when idle.

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Docker Desktop
- At least one LLM API key (OpenAI recommended)

### 1. Clone & Install

```bash
cd autonomous_anomaly_response_agent
pip install poetry
poetry install
```

### 2. Configure Environment

```bash
copy .env.example .env
# Edit .env with your API keys (minimum: OPENAI_API_KEY)
```

### 3. Start Infrastructure

```bash
docker compose up -d
```

This starts: Kafka, Redis, PostgreSQL/pgvector, N8n, Prometheus, Grafana, Loki, Tempo, OTel Collector, Langfuse, and Arize Phoenix.

### 4. Run Demo

```bash
# Quick demo (5 events, 2 anomalies)
poetry run python main.py --mode demo

# Streaming mode (60 seconds of synthetic telemetry)
poetry run python main.py --mode stream --duration 60

# Start the REST API
poetry run uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Run Tests

```bash
poetry run pytest tests/ -v
```

---

## 📁 Project Structure

```
├── agents/
│   ├── monitoring/       # LangChain ReAct agent + 5 tools
│   ├── diagnosis/        # LangGraph DAG + CrewAI crew (3 sub-agents)
│   ├── action/           # N8n client + 3-tier classification
│   └── feedback/         # Contextual bandit RL + reward function
├── data_pipeline/
│   ├── connectors/       # Kafka config + synthetic producer
│   └── flink_jobs/       # Feature extraction + alert dedup
├── knowledge_base/
│   ├── ingestion/        # Document chunking + embedding pipeline
│   ├── retrieval/        # Hybrid search (vector + BM25 + RRF)
│   └── migrations/       # pgvector schema (auto-applied)
├── observability/        # Prometheus, Grafana, Tempo, OTel configs
├── tests/                # Unit, integration, LLM evals
├── shared/               # Pydantic schemas, config, utilities
├── main.py               # CLI orchestrator
├── api.py                # FastAPI REST API
└── docker-compose.yml    # Full local dev stack
```

---

## 🔑 API Keys Required

| Service | Required? | Free Tier? | Purpose |
|---------|-----------|-----------|---------|
| **OpenAI** | ✅ Minimum 1 LLM | $5 free credit | GPT-4o for reasoning |
| **LangSmith** | Recommended | ✅ 5K traces/mo | LLM observability |
| **Langfuse** | Optional | ✅ Self-Hosted | LLM Tracing & Observability |
| **Slack** | Optional | ✅ | Notifications & approvals |
| **PagerDuty** | Optional | 14-day trial | Incident data source |
| **Google Cloud** | Optional | $300 credit | Vertex AI, GKE (Using ADC) |

---

## 💻 API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | System health check |
| `POST` | `/api/v1/events/process` | Process single telemetry event |
| `POST` | `/api/v1/demo/run` | Run demo with synthetic events |
| `POST` | `/api/v1/stream/start` | Start streaming simulation |
| `GET` | `/api/v1/incidents` | List recent incidents |
| `GET` | `/api/v1/incidents/{id}` | Get incident details |
| `GET` | `/api/v1/status` | Agent system status |
| `GET` | `/api/v1/feedback/policy` | RL policy status |
| `GET` | `/api/v1/actions/tiers` | Action tier reference |

---

## 🧪 Testing

```bash
# All tests
poetry run pytest tests/ -v

# Unit tests only
poetry run pytest tests/unit/ -v

# With coverage
poetry run pytest tests/ --cov=agents --cov=shared --cov-report=html

# Linting
poetry run ruff check .

# Type checking
poetry run mypy agents/ shared/ --ignore-missing-imports
```

---

## 📊 Observability

| Service | URL | Credentials |
|---------|-----|-------------|
| **Grafana** | http://localhost:3000 | admin / agent_admin_2024 |
| **Langfuse** | http://localhost:3001 | — |
| **Arize Phoenix** | http://localhost:6006 | — |
| **Prometheus** | http://localhost:9090 | — |
| **N8n** | http://localhost:5678 | admin / agent_admin_2024 |

---

## 🗓️ Sprint Roadmap

| Sprint | Weeks | Focus |
|--------|-------|-------|
| 1–2 | 1–4 | Foundation: scaffolding, Docker Compose, monitoring agent |
| 3–4 | 5–8 | Data pipeline, RAG pipeline, knowledge base |
| 5–7 | 9–14 | All agents live, N8n workflows, Slack integration |
| 8 | 15–16 | RL feedback loop, hardening, integration tests |

---

## 🚢 CI/CD & Deployment

This project utilizes highly automated GitHub Actions for Continuous Integration and Continuous Deployment (CI/CD) to Google Cloud Platform. 

- **Infrastructure as Code**: Core GCP resources (GKE Autopilot, Pub/Sub, Artifact Registry) are rigidly managed via Terraform in `infra/terraform`.
- **Automated Testing**: On every commit, GitHub Actions runs `ruff` to enforce python styling and `pytest` for unit testing and mocked integration validations.
- **Continuous Deployment (CD)**:
  1. The pipeline authenticates securely using a CI/CD Google Service Account.
  2. A streamlined Docker image is built remotely and pushed to **Google Artifact Registry**.
  3. The Kubernetes manifest (`infra/k8s/deployment.yaml`) is dynamically updated with the precise Git commit SHA tag.
  4. The manifest is immediately applied to the **GKE Autopilot** cluster.
  5. The pipeline monitors `kubectl rollout status` to guarantee zero-downtime Pod deployment.

To control the Kubernetes deployment manually:
```bash
# Apply standard infrastructure
kubectl apply -f infra/k8s/deployment.yaml

# Manage scaling instantly (Autopilot drops resources to 0 when idle)
kubectl scale deployment anomaly-agent --replicas=1
```

---

## 🔒 Security & Authentication

This system follows the principle of least privilege and uses modern authentication standards to avoid hardcoded secrets.

### Google Cloud (Vertex AI)
We use **Application Default Credentials (ADC)**. Do **NOT** hardcode service account JSON keys in `.env`.

- **Local Development**: Run the following command and follow the browser prompts:
  ```bash
  gcloud auth application-default login
  ```
- **GCP Production (GKE/Cloud Run)**: The system automatically uses the attached service account. Ensure the service account has the `Vertex AI User` role.
- **Other Environments**: Use [Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation) to swap external credentials for short-lived Google Cloud tokens.

---

## 📝 License

Private — Internal Use Only

---

## 🚀 Recent Implementations & Architectural Evolution (v2)

Significant enhancements have been made to the system's cognitive capabilities, security posture, and production readiness.

### 1. Supervisor-Expert Diagnosis Model (LangGraph)
The Diagnosis Agent has been refactored from a linear 4-node DAG to a more sophisticated **Supervisor-Expert model**. This architecture mimics a real-world SRE triage process:
- **Triage Supervisor**: Analyzes the initial anomaly and dispatches specialized experts in parallel.
- **Parallel Expert Investigations**:
  - **DatabaseExpert**: Deep-dives into connection pools, slow queries, and deadlock traces.
  - **NetworkExpert**: Inspects VPC flow logs, latency between microservices, and ingress bottlenecks.
  - **SecurityAuditor**: Reviews recent IAM changes and looks for adversarial patterns in telemetry.
  - **ApplicationExpert**: Analyzes heap dumps, garbage collection pauses, and stack traces.
- **Result Synthesis**: The Supervisor combines expert findings into a high-confidence Root Cause Analysis (RCA).

### 2. Production Security & Secret Management
We have hardened the production environment on **GKE Autopilot** with a focus on zero-trust and secret safety:
- **GCP Secret Manager CSI Driver**: Sensitive API keys (OpenAI, PagerDuty, Slack) are no longer passed as environment variables. They are securely mounted into the pod filesystem at `/mnt/secrets` and automatically rotated.
- **Workload Identity (ADC)**: The system utilizes Google Cloud Workload Identity, allowing pods to authenticate to Vertex AI and Pub/Sub using IAM roles without needing service account JSON keys.
- **Vulnerability Remediation**: Implementation of automated security gates and container hardening (addressing VULN-011 through VULN-018).

### 3. Advanced Third-Party Integrations
- **PagerDuty Integration**: The agent now fetches full incident context directly from PagerDuty, allowing it to correlate new anomalies with existing outages.
- **Slack Interactive Approvals**: For "Tier 2" remediation actions (e.g., scaling replicas), the agent posts an interactive block to Slack, requiring an SRE's button-click approval before execution.
- **Idempotent N8n Workflows**: All action workflows now support **idempotency keys** and **automatic rollbacks** if the remediation fails to resolve the metric deviation.

### 4. Chaos Engineering & Resilience Testing
A new suite of chaos experiments (`scripts/run_chaos_experiments.py`) has been added to validate system robustness:
- **Distributed Amnesia**: Simulates Redis outages to ensure the Feedback Loop Agent fails gracefully.
- **Stubborn Tool Faults**: Mocks 500 errors from automation endpoints to verify the agent's retry logic and escalation boundaries.
- **Adversarial Injection**: Tests the system's resilience against "Prompt Injection" attacks embedded within telemetry log payloads.

### 5. Specialized Agent Verification
The `scripts/verify_specialized_agents.py` tool provides a benchmark for expert agents, ensuring their reasoning remains sharp and within the assigned domain boundaries (Data, Network, Security).
