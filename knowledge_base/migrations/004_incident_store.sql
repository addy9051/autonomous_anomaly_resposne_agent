-- Durable incident store (replaces the SQLite file that pinned the API to one task).
-- Named incident_records to avoid the older, differently shaped `incidents` table in 001_initial.sql.

CREATE TABLE IF NOT EXISTS incident_records (
    incident_id TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    data        JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incident_records_status ON incident_records (status);
CREATE INDEX IF NOT EXISTS idx_incident_records_updated ON incident_records (updated_at DESC);

CREATE TABLE IF NOT EXISTS incident_audit_trail (
    id          BIGSERIAL PRIMARY KEY,
    incident_id TEXT NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    details     JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incident_audit_incident ON incident_audit_trail (incident_id, timestamp);
