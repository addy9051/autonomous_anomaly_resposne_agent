# 🚀 Autonomous SRE Intelligence Layer — Lean SaaS Production Edition

A **multi-tenant, pay-per-token AI SRE incident copilot and governed autonomous response platform**. Designed to run as a full SaaS product with **$0 fixed GPU cost** and **near-zero idle hosting expenses ($0 to $7 / month)**.

> 📖 **Architecture & Engineering Trade-Offs:** Detailed design rationale, ADRs, security boundaries, and unit economics are documented in [**`docs/DESIGN_DECISIONS.md`**](docs/DESIGN_DECISIONS.md).

---

## 🌟 What This SaaS Does

```
Alert Ingestion (Alertmanager / Datadog / Webhook)
       │
       ▼
Tenant Isolation & Deduplication (Episodic Memory in Postgres)
       │
       ▼
Evidence Gathering (Prometheus PromQL, Datadog Metrics, or Simulator)
       │
       ▼
Multi-Tenant Hybrid RAG (pgvector Cosine + Postgres Full-Text Search)
       │
       ▼
Diagnosis Engine (Evidence Citations E1, E2... with Confidence Capping)
       │
       ▼
Deterministic Policy Gate
  ├─ Critical Severity ────► RCA Only, Escalated to Human (No Auto Action)
  ├─ Tier 2 Actions ───────► SRE Approval Click Required (Tamper-Proof Hash)
  └─ Tier 1 Reversible ────► Auto-Remediation (Rate-Limited, if enabled)
       │
       ▼
Closed-Loop Verifier (Independently Confirms Telemetry Recovery)
       │
       ▼
Live Command Center Dashboard & Executive SRE Brief
```

---

## 🔑 Settings & API Keys You Need to Provide

To run this in production, you only need **2 main credentials** (both have generous free or pay-per-use tiers):

| Required Item | Recommended Free/Cheap Provider | Where to Get It | Monthly Cost |
| :--- | :--- | :--- | :--- |
| **1. Database DSN (`DATABASE_URL`)** | **Neon.tech** (or **Supabase**) | [Neon Console](https://console.neon.tech) | **$0** (Free Tier: 0.5 GB, serverless scale-to-zero, native `pgvector`) |
| **2. LLM API Key (`LLM_API_KEY`)** | **OpenAI** (`gpt-4o-mini`) or **OpenRouter** (`DeepSeek-V3`) | [OpenAI API Keys](https://platform.openai.com/api-keys) or [OpenRouter Keys](https://openrouter.ai/keys) | **~$0.002 per incident** (~$0.20 to $1.00 / month) |
| **3. Master Encryption Key (`ENCRYPTION_MASTER_KEY`)** | Built-in Fernet generator | Run `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | **$0** |
| **4. Tracing (Optional)** | **Langfuse Cloud** | [Langfuse Signup](https://cloud.langfuse.com) | **$0** (Free Hobby tier: 50,000 traces/mo) |

---

## ⚡ 60-Second Database Setup (Neon or Supabase)

1. Sign up for a free project at [Neon.tech](https://neon.tech) or [Supabase.com](https://supabase.com).
2. Open the project's **SQL Editor**.
3. Copy the contents of [`database/setup_supabase.sql`](database/setup_supabase.sql) and click **Run**.
4. Copy your PostgreSQL Connection String (DSN) and set it as `DATABASE_URL`.

---

## 🚢 Deployment Options ($0 to $7 / month)

### Option 1: 1-Click Deploy on Render.com ($7/mo or Free Web Service)
1. Push this folder to your GitHub repository.
2. In the [Render Dashboard](https://dashboard.render.com), click **New +** → **Blueprint**.
3. Select your repository. Render will automatically read [`deploy/render.yaml`](deploy/render.yaml).
4. Enter your `LLM_API_KEY` when prompted and click **Apply**.
5. Your SaaS backend and Command Center will be live at `https://your-service.onrender.com/dashboard`!

### Option 2: Deploy to Google Cloud Run ($0/mo Idle — Scales to 0)
1. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
2. Run the deployment script from this directory:
   ```bash
   chmod +x deploy/cloud_run_deploy.sh
   ./deploy/cloud_run_deploy.sh
   ```
3. Cloud Run provides an automated HTTPS URL with 2 million free requests every month.

### Option 3: Deploy to Fly.io ($0/mo Idle)
```bash
fly launch
fly secrets set DATABASE_URL="postgresql://..." LLM_API_KEY="sk-..." ENCRYPTION_MASTER_KEY="..."
fly deploy
```

### Option 4: Local Full-Stack Test (Docker Compose)
To test the complete stack on your development machine:
```bash
docker compose up -d
```
Open `http://localhost:8000/dashboard` in your browser.

---

## 🖥️ Interactive Command Center

The application includes a built-in, zero-dependency modern Command Center dashboard at `/dashboard`.

* **Live Stream:** Real-time stream of incoming incidents.
* **Root-Cause Analysis:** Evidence citations ($E_1, E_2$) and confidence metrics.
* **Human-in-the-Loop Approval:** 1-click **Approve & Execute** or **Reject** with SHA-256 proposal hash validation.
* **Simulation Bar:** Built-in triggers to test memory spikes and 504 timeouts.

---

## 📡 Webhook Integration for Telemetry

Point your monitoring tools to your deployed URL:

### 1. Prometheus Alertmanager
Add to `alertmanager.yml`:
```yaml
receivers:
  - name: 'sre-ai-copilot'
    webhook_configs:
      - url: 'https://<your-deployed-domain>/api/v1/alerts/alertmanager'
        send_resolved: true
        http_config:
          bearer_token: '<your-tenant-api-key>'
```

### 2. Universal Webhook (Datadog, Grafana, CloudWatch)
Send a JSON payload to `POST /api/v1/alerts/webhook` with header `X-API-Key: <your-tenant-api-key>`:
```json
{
  "title": "Payment API 5xx Spikes > 8%",
  "service": "payment-api",
  "anomaly_type": "HTTPErrorSpike",
  "severity": "high",
  "summary": "Sudden latency increase and error spikes on /v1/checkout"
}
```

---

## 🛡️ Security & Multi-Tenancy Architecture

* **Database Isolation:** All incidents, episodes, runbooks, and audit logs are strictly partitioned by `tenant_id`.
* **Credential Encryption:** Telemetry vendor API keys (Datadog, Prometheus) are encrypted at rest in PostgreSQL using AES-128-CBC Fernet symmetric encryption.
* **Deterministic Safety Gate:** Critical incidents can never be auto-remediated by the LLM. They are automatically gated to root-cause analysis and human on-call escalation.
