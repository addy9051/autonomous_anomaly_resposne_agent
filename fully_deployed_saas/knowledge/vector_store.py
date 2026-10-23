"""
Multi-Tenant Hybrid RAG Store (pgvector Cosine + Postgres Full-Text Search).
Strict tenant boundary enforcement: Customer A can never access Customer B's runbooks.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from core.config import get_settings
from core.llm import get_embeddings
from core.utils import get_logger
from database.connection import execute, fetch_all

logger = get_logger("vector_store")


_has_pg_textsearch: bool | None = None
_has_vectorscale: bool | None = None


async def check_search_extensions() -> tuple[bool, bool]:
    """Check if pg_textsearch and vectorscale extensions are active in PostgreSQL."""
    global _has_pg_textsearch, _has_vectorscale
    if _has_pg_textsearch is not None and _has_vectorscale is not None:
        return _has_pg_textsearch, _has_vectorscale
    try:
        exts = await fetch_all("SELECT extname FROM pg_extension WHERE extname IN ('pg_textsearch', 'vectorscale')")
        names = {r["extname"] for r in exts}
        _has_pg_textsearch = "pg_textsearch" in names
        _has_vectorscale = "vectorscale" in names
        logger.info("postgres_search_extensions", pg_textsearch=_has_pg_textsearch, pgvectorscale=_has_vectorscale)
    except Exception as exc:
        logger.warning("extension_check_failed_fallback_default", error=str(exc))
        _has_pg_textsearch = False
        _has_vectorscale = False
    return _has_pg_textsearch, _has_vectorscale


class MultiTenantRAG:
    """Manages tenant-isolated vector embeddings and hybrid document search."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.settings = get_settings()

    async def ingest_document(
        self,
        title: str,
        content: str,
        collection: str = "application",
        service_tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Embed and store a runbook chunk under the tenant's namespace."""
        service_tags = service_tags or []
        metadata = metadata or {}

        # 1. Generate embedding vector (with fallback if external API key is invalid)
        try:
            embeddings_client = get_embeddings()
            vector = await embeddings_client.aembed_query(f"{title}\n{content}")
            vector_str = f"[{','.join(str(x) for x in vector)}]"
        except Exception as exc:
            logger.warning("vector_embed_failed_fallback_dummy", error=str(exc))
            vector_str = f"[{','.join(['0.0'] * self.settings.embedding_dimensions)}]"

        # 2. Insert into PostgreSQL
        doc_id = str(uuid4())
        query = """
            INSERT INTO documents (
                id, tenant_id, collection, title, content, service_tags, embedding, metadata
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7::vector, $8::jsonb
            )
        """
        await execute(
            query,
            doc_id,
            self.tenant_id,
            collection,
            title,
            content,
            service_tags,
            vector_str,
            json.dumps(metadata),
        )
        logger.info("document_ingested", tenant_id=self.tenant_id, doc_id=doc_id, title=title)
        return doc_id

    async def hybrid_search(
        self,
        query: str,
        collection: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Execute tenant-isolated Hybrid Search:
        1. Dense Vector Similarity (DiskANN via pgvector-scale or HNSW pgvector)
        2. Sparse BM25 Search (pg_textsearch with <@> operator, or tsvector fallback)
        3. Reciprocal Rank Fusion (RRF)
        """
        use_bm25, use_diskann = await check_search_extensions()

        # Vector embedding of query
        try:
            embeddings_client = get_embeddings()
            vector = await embeddings_client.aembed_query(query)
            vector_str = f"[{','.join(str(x) for x in vector)}]"
        except Exception as exc:
            logger.warning("vector_embed_failed_fallback_to_text", error=str(exc))
            vector_str = None

        # Build text search CTE based on extension availability
        if use_bm25:
            # Native pg_textsearch BM25 (<@> returns negative relevance score; lower is better)
            text_cte = """
                text_matches AS (
                    SELECT id, title, content, collection, service_tags,
                           (content <@> $4) AS text_score,
                           ROW_NUMBER() OVER (ORDER BY (content <@> $4) ASC) AS rank_t
                    FROM documents
                    WHERE tenant_id = $1
                      AND ($3::text IS NULL OR collection = $3)
                    ORDER BY (content <@> $4) ASC
                    LIMIT 25
                )
            """
        else:
            # Fallback to PostgreSQL built-in tsvector GIN text search
            text_cte = """
                text_matches AS (
                    SELECT id, title, content, collection, service_tags,
                           ts_rank_cd(tsv, plainto_tsquery('english', $4)) AS text_score,
                           ROW_NUMBER() OVER (ORDER BY ts_rank_cd(tsv, plainto_tsquery('english', $4)) DESC) AS rank_t
                    FROM documents
                    WHERE tenant_id = $1
                      AND ($3::text IS NULL OR collection = $3)
                      AND tsv @@ plainto_tsquery('english', $4)
                    LIMIT 25
                )
            """

        if vector_str:
            sql = f"""
                WITH vector_matches AS (
                    SELECT id, title, content, collection, service_tags,
                           1.0 - (embedding <=> $2::vector) AS vector_score,
                           ROW_NUMBER() OVER (ORDER BY embedding <=> $2::vector ASC) AS rank_v
                    FROM documents
                    WHERE tenant_id = $1
                      AND ($3::text IS NULL OR collection = $3)
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> $2::vector ASC
                    LIMIT 25
                ),
                {text_cte}
                SELECT
                    COALESCE(v.id, t.id) AS id,
                    COALESCE(v.title, t.title) AS title,
                    COALESCE(v.content, t.content) AS content,
                    COALESCE(v.collection, t.collection) AS collection,
                    COALESCE(v.service_tags, t.service_tags) AS service_tags,
                    COALESCE(v.vector_score, 0.0) AS vector_score,
                    (
                        COALESCE(1.0 / (60.0 + v.rank_v), 0.0) +
                        COALESCE(1.0 / (60.0 + t.rank_t), 0.0)
                    ) AS rrf_score
                FROM vector_matches v
                FULL OUTER JOIN text_matches t ON v.id = t.id
                ORDER BY rrf_score DESC
                LIMIT $5;
            """
            rows = await fetch_all(sql, self.tenant_id, vector_str, collection, query, top_k)
            return rows

        # Fallback to pure keyword search if embedding generation failed
        if use_bm25:
            fallback_sql = """
                SELECT id, title, content, collection, service_tags,
                       (content <@> $2) AS rrf_score
                FROM documents
                WHERE tenant_id = $1
                  AND ($3::text IS NULL OR collection = $3)
                ORDER BY (content <@> $2) ASC
                LIMIT $4;
            """
        else:
            fallback_sql = """
                SELECT id, title, content, collection, service_tags,
                       ts_rank_cd(tsv, plainto_tsquery('english', $2)) AS rrf_score
                FROM documents
                WHERE tenant_id = $1
                  AND ($3::text IS NULL OR collection = $3)
                ORDER BY rrf_score DESC
                LIMIT $4;
            """
        return await fetch_all(fallback_sql, self.tenant_id, query, collection, top_k)
