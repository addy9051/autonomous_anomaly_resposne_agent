-- =============================================================================
-- Multi-Tenant SaaS Database Schema for AI SRE Autonomous Response Platform
-- Compatible with: Neon.tech, Supabase, AWS RDS PostgreSQL 15+, Render Postgres
-- Requires: pgvector extension (available in all modern managed Postgres services)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Timescale pgvector-scale: DiskANN Streaming Vector Index & Statistical Binary Quantization
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vectorscale CASCADE;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'vectorscale extension not available, falling back to standard pgvector (HNSW/IVFFlat)';
END $$;

-- pg_textsearch: Native Industry-Standard BM25 Relevance-Ranked Search
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS pg_textsearch;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'pg_textsearch extension not available, falling back to standard tsvector GIN';
END $$;

-- 1. Organizations / Tenants
CREATE TABLE IF NOT EXISTS tenants (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    plan VARCHAR(32) NOT NULL DEFAULT 'starter', -- starter, pro, enterprise
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    max_monthly_incidents INT NOT NULL DEFAULT 500,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Tenant API Keys (for Telemetry Ingestion Webhooks & Agent SDK)
CREATE TABLE IF NOT EXISTS tenant_api_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key_hash VARCHAR(64) NOT NULL UNIQUE, -- SHA-256 hash of the API key
    key_prefix VARCHAR(12) NOT NULL,      -- e.g. "sre_live_..."
    name VARCHAR(128) NOT NULL DEFAULT 'Default Ingestion Key',
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_lookup ON tenant_api_keys(key_hash) WHERE is_revoked = FALSE;
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON tenant_api_keys(tenant_id);

-- 3. Tenant Telemetry Connectors (Encrypted Credentials for Datadog, Prometheus, etc.)
CREATE TABLE IF NOT EXISTS tenant_connectors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    vendor VARCHAR(32) NOT NULL, -- prometheus, datadog, splunk, cloudwatch
    name VARCHAR(128) NOT NULL,
    config_encrypted TEXT NOT NULL, -- Fernet AES-128-CBC / HMAC encrypted JSON
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_vendor UNIQUE(tenant_id, vendor)
);

CREATE INDEX IF NOT EXISTS idx_connectors_tenant ON tenant_connectors(tenant_id);

-- 4. Incidents (Multi-Tenant Incident Lifecycle)
CREATE TABLE IF NOT EXISTS incidents (
    incident_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title VARCHAR(512) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'investigating', -- investigating, recommendation_ready, awaiting_approval, executing, verifying, resolved, escalated
    severity VARCHAR(32) NOT NULL DEFAULT 'medium',      -- low, medium, high, critical
    service VARCHAR(128) NOT NULL DEFAULT 'unknown',
    fingerprint VARCHAR(128),
    root_cause TEXT,
    confidence NUMERIC(4, 3) DEFAULT 0.0,
    hypotheses JSONB DEFAULT '[]'::jsonb,
    evidence_summary JSONB DEFAULT '{}'::jsonb,
    timeline JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_incidents_tenant_status ON incidents(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_incidents_created ON incidents(tenant_id, created_at DESC);

-- 5. Episodic Memory (Cross-Incident Learning & Fingerprint Deduplication)
CREATE TABLE IF NOT EXISTS episodes (
    episode_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    fingerprint VARCHAR(128) NOT NULL,
    anomaly_type VARCHAR(64) NOT NULL,
    severity VARCHAR(32) NOT NULL,
    services JSONB NOT NULL DEFAULT '[]'::jsonb,
    outcome VARCHAR(64) NOT NULL, -- resolved, recovered, escalated, suppressed
    recovery_status VARCHAR(64),
    brief TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_episodes_tenant_fp ON episodes(tenant_id, fingerprint, created_at DESC);

-- 6. Knowledge Base Documents / Runbooks (Multi-Tenant Hybrid RAG)
-- Standard OpenAI text-embedding-3-small dimension is 1536 (or 768 for BGE / Gemini)
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    collection VARCHAR(64) NOT NULL DEFAULT 'application', -- database, network, security, application, policy
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    service_tags TEXT[] DEFAULT '{}',
    embedding vector(1536), -- pgvector column
    metadata JSONB DEFAULT '{}'::jsonb,
    tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', title || ' ' || content)) STORED,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_documents_tenant_collection ON documents(tenant_id, collection);
CREATE INDEX IF NOT EXISTS idx_documents_tsv ON documents USING gin(tsv);

-- 1. Semantic Search Vector Index:
-- Priority: DiskANN (pgvector-scale) -> HNSW (pgvector) -> IVFFlat
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname IN ('idx_documents_diskann', 'idx_documents_embedding')) THEN
        BEGIN
            -- Attempt pgvector-scale StreamingDiskANN index
            EXECUTE 'CREATE INDEX idx_documents_diskann ON documents USING diskann (embedding vector_cosine_ops);';
            RAISE NOTICE 'Created DiskANN vector index via pgvector-scale';
        EXCEPTION WHEN OTHERS THEN
            BEGIN
                -- Fallback to standard pgvector HNSW
                EXECUTE 'CREATE INDEX idx_documents_embedding ON documents USING hnsw (embedding vector_cosine_ops);';
                RAISE NOTICE 'Created HNSW vector index via pgvector';
            EXCEPTION WHEN OTHERS THEN
                -- Fallback to IVFFlat
                EXECUTE 'CREATE INDEX idx_documents_embedding ON documents USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);';
            END;
        END;
    END IF;
END $$;

-- 2. Keyword Search Index:
-- Priority: pg_textsearch BM25 index -> tsvector GIN index
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'idx_documents_bm25') THEN
        BEGIN
            EXECUTE 'CREATE INDEX idx_documents_bm25 ON documents USING bm25(content) WITH (text_config = ''english'');';
            RAISE NOTICE 'Created BM25 index via pg_textsearch';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'pg_textsearch not active, standard tsvector GIN index will be used';
        END;
    END IF;
END $$;

-- 7. Action Proposals & Approvals (Tamper-Proof Deterministic Governance)
CREATE TABLE IF NOT EXISTS action_proposals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id VARCHAR(64) NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tier INT NOT NULL DEFAULT 2, -- 1: auto, 2: approval required, 3: human only
    action_type VARCHAR(64) NOT NULL,
    target_service VARCHAR(128) NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    proposal_hash VARCHAR(64) NOT NULL, -- SHA-256 of canonical action parameters
    status VARCHAR(32) NOT NULL DEFAULT 'pending', -- pending, approved, rejected, executing, completed, failed
    approved_by VARCHAR(128),
    approval_reason TEXT,
    execution_result JSONB,
    verifier_outcome VARCHAR(32), -- recovered, not_recovered, inconclusive
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_proposals_incident ON action_proposals(tenant_id, incident_id);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON action_proposals(tenant_id, status);

-- 8. Audit Log
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    event_type VARCHAR(64) NOT NULL,
    actor VARCHAR(128) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_tenant_time ON audit_logs(tenant_id, created_at DESC);

-- Seed default demo tenant if not exists
INSERT INTO tenants (id, name, plan)
VALUES ('tenant-demo', 'Demo SRE Team', 'pro')
ON CONFLICT (id) DO NOTHING;
