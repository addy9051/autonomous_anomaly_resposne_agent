-- BM25 inside Postgres via ParadeDB pg_search (requires the paradedb/paradedb image or the pg_search extension).
-- Replaces the in-process BM25 scan; the Python BM25 remains only as a fallback when pg_search is unavailable.
-- Existing databases: apply this file manually, then run ANALYZE documents.

CREATE EXTENSION IF NOT EXISTS pg_search;

-- title and content use the default tokenizer; collection is a literal so it can be filtered inside the index.
-- `id` is the indexed row key used by pdb.score(id).
CREATE INDEX IF NOT EXISTS idx_documents_bm25
    ON documents USING bm25 (id, title, content, (collection::pdb.literal));
