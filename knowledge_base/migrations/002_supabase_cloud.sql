-- ═══════════════════════════════════════════════════════════════
--  Supabase Cloud pgvector Schema Migration — Knowledge Base
--  
--  HOW TO RUN:
--  1. Go to your Supabase Dashboard → SQL Editor
--  2. Paste this entire file and click "Run"
--  3. Then seed: python scripts/seed_knowledge_base.py
-- ═══════════════════════════════════════════════════════════════

-- Enable required extensions (Supabase has these available)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ─── Runbook Documents ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS documents (
    id              SERIAL PRIMARY KEY,
    doc_id          TEXT UNIQUE NOT NULL,
    title           TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'manual',
    content         TEXT NOT NULL,
    chunk_index     INTEGER NOT NULL DEFAULT 0,
    total_chunks    INTEGER NOT NULL DEFAULT 1,
    embedding       vector(768),                  -- 768 dims: text-embedding-3-small with Matryoshka truncation
    metadata        JSONB DEFAULT '{}',
    service_tags    TEXT[] DEFAULT '{}',
    severity_relevance TEXT[] DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for approximate nearest neighbor search
CREATE INDEX IF NOT EXISTS idx_documents_embedding
    ON documents USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 200);

-- B-tree indexes for metadata filtering
CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source);
CREATE INDEX IF NOT EXISTS idx_documents_service_tags ON documents USING GIN(service_tags);
CREATE INDEX IF NOT EXISTS idx_documents_severity ON documents USING GIN(severity_relevance);

-- Trigram indexes for keyword search
CREATE INDEX IF NOT EXISTS idx_documents_content_trgm
    ON documents USING GIN(content gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_documents_title_trgm
    ON documents USING GIN(title gin_trgm_ops);


-- ─── Incident History ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS incidents (
    id                      SERIAL PRIMARY KEY,
    incident_id             TEXT UNIQUE NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'detected',
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    updated_at              TIMESTAMPTZ DEFAULT NOW(),
    resolved_at             TIMESTAMPTZ,

    anomaly_event           JSONB,
    severity                TEXT,
    anomaly_type            TEXT,
    affected_services       TEXT[],

    diagnosis_result        JSONB,
    root_cause              TEXT,
    root_cause_category     TEXT,
    confidence              FLOAT,

    action_results          JSONB DEFAULT '[]',
    actions_taken           TEXT[],

    time_to_detect_seconds  FLOAT,
    time_to_mitigate_seconds FLOAT,
    auto_resolved           BOOLEAN DEFAULT FALSE,
    false_positive          BOOLEAN DEFAULT FALSE,
    human_overrode          BOOLEAN DEFAULT FALSE,
    human_feedback          TEXT,

    total_llm_tokens_used   INTEGER DEFAULT 0,
    total_llm_cost_usd      FLOAT DEFAULT 0.0,

    reward                  FLOAT,
    state_features          FLOAT[],
    action_label            TEXT
);

CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_incidents_severity ON incidents(severity);
CREATE INDEX IF NOT EXISTS idx_incidents_root_cause ON incidents(root_cause_category);


-- ─── Agent Audit Log ────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS agent_audit_log (
    id              SERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    agent_name      TEXT NOT NULL,
    incident_id     TEXT,
    action_type     TEXT NOT NULL,
    action_details  JSONB DEFAULT '{}',
    reasoning       TEXT,
    confidence      FLOAT,
    llm_model       TEXT,
    tokens_used     INTEGER DEFAULT 0,
    cost_usd        FLOAT DEFAULT 0.0,
    execution_ms    FLOAT DEFAULT 0.0,
    status          TEXT DEFAULT 'success'
);

CREATE INDEX IF NOT EXISTS idx_audit_agent ON agent_audit_log(agent_name);
CREATE INDEX IF NOT EXISTS idx_audit_incident ON agent_audit_log(incident_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON agent_audit_log(timestamp DESC);


-- ─── RL Training Data ───────────────────────────────────────────

CREATE TABLE IF NOT EXISTS rl_training_data (
    id              SERIAL PRIMARY KEY,
    incident_id     TEXT NOT NULL REFERENCES incidents(incident_id),
    state_features  FLOAT[] NOT NULL,
    action          TEXT NOT NULL,
    reward          FLOAT NOT NULL,
    policy_version  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rl_created ON rl_training_data(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rl_policy ON rl_training_data(policy_version);
