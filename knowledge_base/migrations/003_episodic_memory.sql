-- ═══════════════════════════════════════════════════════════════
--  003: Per-expert RAG collections + episodic memory
-- ═══════════════════════════════════════════════════════════════

-- Knowledge collections: database | network | security | application | policy | general
ALTER TABLE documents ADD COLUMN IF NOT EXISTS collection TEXT NOT NULL DEFAULT 'general';
CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);

-- ─── Episodic memory ────────────────────────────────────────────
-- One row per handled event (episode); a duplicate fingerprint points back at the original.

CREATE TABLE IF NOT EXISTS episodes (
    episode_id      TEXT PRIMARY KEY,
    fingerprint     TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    severity        TEXT NOT NULL,
    anomaly_type    TEXT NOT NULL,
    services        TEXT[] NOT NULL DEFAULT '{}',
    branch          TEXT NOT NULL,              -- critical_rca | auto_fix | recommend
    status          TEXT NOT NULL DEFAULT 'open', -- open | resolved | escalated | failed
    root_cause      TEXT,
    decision        JSONB NOT NULL DEFAULT '{}',
    action          TEXT,
    outcome         TEXT,                        -- recovered | not_recovered | inconclusive | none
    duplicate_of    TEXT REFERENCES episodes(episode_id),
    skill           TEXT,
    brief           JSONB
);

CREATE INDEX IF NOT EXISTS idx_episodes_fingerprint ON episodes(fingerprint, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status);

-- Append-only record of what happened at each timestamp
CREATE TABLE IF NOT EXISTS episode_events (
    id          BIGSERIAL PRIMARY KEY,
    episode_id  TEXT NOT NULL REFERENCES episodes(episode_id),
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    stage       TEXT NOT NULL,                  -- intake | triage | evidence | diagnosis | policy | action | verify | report
    actor       TEXT NOT NULL,
    detail      JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_episode_events_episode ON episode_events(episode_id, ts);
