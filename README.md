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
| **Google Cloud** | Optional | $300 credit | Vertex AI, GKE (production) |

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

## 📝 License

Private — Internal Use Only
