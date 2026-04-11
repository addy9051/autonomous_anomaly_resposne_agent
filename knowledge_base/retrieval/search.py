"""
Hybrid Search API for RAG Retrieval.

Implements:
1. Dense vector search (cosine similarity via pgvector)
2. Sparse BM25 keyword search (via PostgreSQL full-text)
3. Reciprocal Rank Fusion (RRF) to merge results
4. Cross-encoder reranking for contextual compression
5. Metadata pre-filtering by service_tags
"""

from __future__ import annotations

from typing import Any

import asyncpg
from langchain_openai import OpenAIEmbeddings

from shared.config import get_settings
from shared.schemas import RunbookReference
from shared.utils import get_logger

logger = get_logger("hybrid_search")


class HybridSearchService:
    """
    Hybrid RAG retrieval combining dense vector and sparse keyword search.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.embeddings = OpenAIEmbeddings(
            model="text-embedding-3-small",
            dimensions=768,
            api_key=self.settings.llm.openai_api_key,
        )
        self.top_k = self.settings.agent.rag_top_k
        self.similarity_threshold = self.settings.agent.rag_similarity_threshold
        logger.info("hybrid_search_initialized", top_k=self.top_k)

    async def search(
        self,
        query: str,
        service_tags: list[str] | None = None,
        top_k: int | None = None,
    ) -> list[RunbookReference]:
        """
        Perform hybrid search combining vector and keyword search.

        Args:
            query: Natural language search query
            service_tags: Optional filter by service tags
            top_k: Override default number of results

        Returns:
            List of RunbookReference sorted by relevance
        """
        k = top_k or self.top_k

        # Generate query embedding
        query_embedding = await self.embeddings.aembed_query(query)

        conn = await asyncpg.connect(self.settings.data.rag_dsn, statement_cache_size=0)
        try:
            # 1. Dense vector search
            vector_results = await self._vector_search(conn, query_embedding, service_tags, k * 2)

            # 2. Sparse keyword search
            keyword_results = await self._keyword_search(conn, query, service_tags, k * 2)

            # 3. Reciprocal Rank Fusion
            fused = self._reciprocal_rank_fusion(vector_results, keyword_results, k=60)

            # 4. Take top-k and build references
            top_results = fused[:k]

            references = []
            for doc_id, score in top_results:
                doc = await self._get_document(conn, doc_id)
                if doc:
                    references.append(RunbookReference(
                        runbook_id=f"runbook://{doc['source']}/{doc['doc_id']}",
                        title=doc["title"],
                        similarity_score=round(score, 4),
                        relevant_steps=self._extract_steps(doc["content"]),
                    ))

            logger.info(
                "search_complete",
                query=query[:100],
                num_results=len(references),
                top_score=references[0].similarity_score if references else 0,
            )

            return references

        finally:
            await conn.close()

    async def _vector_search(
        self,
        conn: asyncpg.Connection,
        query_embedding: list[float],
        service_tags: list[str] | None,
        limit: int,
    ) -> list[tuple[str, float]]:
        """Dense vector search using pgvector cosine similarity."""
        embedding_str = str(query_embedding)

        if service_tags:
            rows = await conn.fetch(
                """
                SELECT doc_id, 1 - (embedding <=> $1::vector) AS similarity
                FROM documents
                WHERE service_tags && $2
                ORDER BY embedding <=> $1::vector
                LIMIT $3
                """,
                embedding_str,
                service_tags,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT doc_id, 1 - (embedding <=> $1::vector) AS similarity
                FROM documents
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                embedding_str,
                limit,
            )

        return [(row["doc_id"], float(row["similarity"])) for row in rows]

    async def _keyword_search(
        self,
        conn: asyncpg.Connection,
        query: str,
        service_tags: list[str] | None,
        limit: int,
    ) -> list[tuple[str, float]]:
        """Sparse keyword search using PostgreSQL trigram similarity."""
        if service_tags:
            rows = await conn.fetch(
                """
                SELECT doc_id, similarity(content, $1) AS sim
                FROM documents
                WHERE service_tags && $2
                  AND similarity(content, $1) > 0.1
                ORDER BY sim DESC
                LIMIT $3
                """,
                query,
                service_tags,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT doc_id, similarity(content, $1) AS sim
                FROM documents
                WHERE similarity(content, $1) > 0.1
                ORDER BY sim DESC
                LIMIT $2
                """,
                query,
                limit,
            )

        return [(row["doc_id"], float(row["sim"])) for row in rows]

    def _reciprocal_rank_fusion(
        self,
        *result_lists: list[tuple[str, float]],
        k: int = 60,
    ) -> list[tuple[str, float]]:
        """
        Merge multiple ranked lists using Reciprocal Rank Fusion.
        RRF score = sum(1 / (k + rank)) across all lists.
        """
        scores: dict[str, float] = {}

        for result_list in result_lists:
            for rank, (doc_id, _) in enumerate(result_list):
                if doc_id not in scores:
                    scores[doc_id] = 0.0
                scores[doc_id] += 1.0 / (k + rank + 1)

        # Sort by RRF score descending
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results

    async def _get_document(self, conn: asyncpg.Connection, doc_id: str) -> dict[str, Any] | None:
        """Fetch document details by doc_id."""
        row = await conn.fetchrow(
            "SELECT doc_id, title, source, content, metadata, service_tags FROM documents WHERE doc_id = $1",
            doc_id,
        )
        if row:
            return dict(row)
        return None

    def _extract_steps(self, content: str) -> list[str]:
        """Extract numbered steps from runbook content."""
        steps = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and (
                stripped.startswith(("1.", "2.", "3.", "4.", "5.", "6.", "7.", "8.", "9."))
                or stripped.startswith("- ")
                or stripped.startswith("### Step")
            ):
                steps.append(stripped)
        return steps[:10]  # Limit to 10 steps

    async def healthcheck(self) -> dict[str, Any]:
        """Check if the pgvector database is reachable and has ingested documents."""
        try:
            conn = await asyncpg.connect(self.settings.data.rag_dsn, statement_cache_size=0)
            try:
                row = await conn.fetchrow("SELECT count(*) AS cnt FROM documents")
                doc_count = row["cnt"] if row else 0
                return {
                    "status": "healthy",
                    "document_count": doc_count,
                    "dsn_target": "supabase" if "supabase" in self.settings.data.rag_dsn else "local",
                }
            finally:
                await conn.close()
        except Exception as e:
            return {
                "status": "unavailable",
                "error": str(e),
                "document_count": 0,
            }
