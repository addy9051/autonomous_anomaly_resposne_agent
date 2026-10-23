-- =============================================================================
-- 1-Click Setup Script for Supabase / Neon / Render SQL Editor
-- Instructions:
-- 1. Create a free project at https://neon.tech or https://supabase.com
-- 2. Open the SQL Editor
-- 3. Paste this entire file and click "Run"
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
    plan VARCHAR(32) NOT NULL DEFAULT 'starter',
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    max_monthly_incidents INT NOT NULL DEFAULT 500,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Tenant API Keys
CREATE TABLE IF NOT EXISTS tenant_api_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    key_prefix VARCHAR(12) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT 'Default Key',
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_lookup ON tenant_api_keys(key_hash) WHERE is_revoked = FALSE;
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON tenant_api_keys(tenant_id);

-- 3. Tenant Telemetry Connectors
CREATE TABLE IF NOT EXISTS tenant_connectors (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    vendor VARCHAR(32) NOT NULL,
    name VARCHAR(128) NOT NULL,
    config_encrypted TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_tenant_vendor UNIQUE(tenant_id, vendor)
);

CREATE INDEX IF NOT EXISTS idx_connectors_tenant ON tenant_connectors(tenant_id);

-- 4. Incidents
CREATE TABLE IF NOT EXISTS incidents (
    incident_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    title VARCHAR(512) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'investigating',
    severity VARCHAR(32) NOT NULL DEFAULT 'medium',
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

-- 5. Episodic Memory
CREATE TABLE IF NOT EXISTS episodes (
    episode_id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    fingerprint VARCHAR(128) NOT NULL,
    anomaly_type VARCHAR(64) NOT NULL,
    severity VARCHAR(32) NOT NULL,
    services JSONB NOT NULL DEFAULT '[]'::jsonb,
    outcome VARCHAR(64) NOT NULL,
    recovery_status VARCHAR(64),
    brief TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_episodes_tenant_fp ON episodes(tenant_id, fingerprint, created_at DESC);

-- 6. Knowledge Base Documents (pgvector + full-text search)
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    collection VARCHAR(64) NOT NULL DEFAULT 'application',
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    service_tags TEXT[] DEFAULT '{}',
    embedding vector(1536),
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

-- 7. Action Proposals
CREATE TABLE IF NOT EXISTS action_proposals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    incident_id VARCHAR(64) NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    tenant_id VARCHAR(64) NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tier INT NOT NULL DEFAULT 2,
    action_type VARCHAR(64) NOT NULL,
    target_service VARCHAR(128) NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    proposal_hash VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    approved_by VARCHAR(128),
    approval_reason TEXT,
    execution_result JSONB,
    verifier_outcome VARCHAR(32),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_proposals_incident ON action_proposals(tenant_id, incident_id);

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

-- Demo Organization Initial Seed
INSERT INTO tenants (id, name, plan)
VALUES ('tenant-demo', 'Demo SRE Organization', 'pro')
ON CONFLICT (id) DO NOTHING;

-- Initial API Key: "sre_live_demo1234567890" (hash is SHA256)
-- Hash of 'sre_live_demo1234567890':
INSERT INTO tenant_api_keys (tenant_id, key_hash, key_prefix, name)
VALUES (
    'tenant-demo',
    '33917454ee9458f44ff53e5e4fe4cfb9ee9e0e33ef707a339a1fe186e21b8b80',
    'sre_live_demo',
    'Default Demo Key'
) ON CONFLICT (key_hash) DO NOTHING;
