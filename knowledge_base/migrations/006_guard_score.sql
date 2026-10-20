-- Ingest-time poisoning score (P(unsafe), 0-1) from a content guard such as TypeSafe Jev, so retrieval never
-- has to call a hosted classifier on the incident path. NULL = not scored yet.
-- Existing databases: apply manually, then run `python -m knowledge_base.score_guard` to fill it in.

ALTER TABLE documents
    ADD COLUMN IF NOT EXISTS guard_score REAL,
    ADD COLUMN IF NOT EXISTS guard_model TEXT,
    ADD COLUMN IF NOT EXISTS guard_scored_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_documents_guard_unscored ON documents (id) WHERE guard_score IS NULL;
