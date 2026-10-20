"""
Hybrid Search API for RAG Retrieval.

Implements:
1. Dense vector search (cosine similarity via pgvector)
2. Sparse BM25 keyword search: ParadeDB pg_search inside Postgres (in-process Okapi BM25 in bm25.py as fallback)
3. Local FlashRank cross-encoder rerank of the vector + BM25 candidate union (knowledge_base/retrieval/rerank.py)
4. Weighted Reciprocal Rank Fusion (RRF) over vector, BM25 and reranker rankings
5. Metadata pre-filtering by service_tags
"""

from __future__ import annotations

from typing import Any

import asyncpg
from opentelemetry import trace

from knowledge_base.retrieval.bm25 import BM25Corpus
from knowledge_base.retrieval.rerank import get_reranker
from shared.config import get_settings
from shared.llm import get_embeddings
from shared.schemas import RunbookReference
from shared.utils import get_logger, get_tracer

logger = get_logger("hybrid_search")
tracer = get_tracer()

# Poisoning controls applied inside every retrieval query, so unsafe rows never reach fusion or the reranker.
# $a = exclude untrusted (bool), $b = guard_score ceiling (real, NULL disables). Unscored rows (NULL) pass.
_SAFETY_SQL = (
    "(NOT ${a}::bool OR COALESCE(metadata->>'trust_tier', '') <> 'untrusted') "
    "AND (${b}::real IS NULL OR guard_score IS NULL OR guard_score < ${b}::real)"
)


def _safety(a: int, b: int) -> str:
    return _SAFETY_SQL.replace("{a}", str(a)).replace("{b}", str(b))


class HybridSearchService:
    """
    Hybrid RAG retrieval combining dense vector and sparse keyword search.
    """

    def __init__(self) -> None:
        self.settings = get_settings()
        self.embeddings = get_embeddings()
        self.top_k = self.settings.agent.rag_top_k
        self.similarity_threshold = self.settings.agent.rag_similarity_threshold
        self._paradedb_unavailable = False
        logger.info("hybrid_search_initialized", top_k=self.top_k)

    async def search(
        self,
        query: str,
        service_tags: list[str] | None = None,
        top_k: int | None = None,
        collections: list[str] | None = None,
    ) -> list[RunbookReference]:
        """
        Perform hybrid search combining vector and keyword search.

        Args:
            query: Natural language search query
            service_tags: Optional filter by service tags
            collections: Restrict to these knowledge collections (e.g. an expert's own + "policy")
            top_k: Override default number of results

        Returns:
            List of RunbookReference sorted by relevance
        """
        import time

        start_time = time.time()

        with tracer.start_as_current_span("rag_retrieval") as span:
            span.set_attribute("rag.query", query)
            k = top_k or self.top_k

        # Generate query embedding
        query_embedding = await self.embeddings.aembed_query(query)

        conn = await asyncpg.connect(self.settings.data.rag_dsn, statement_cache_size=0)
        try:
            rerank_on = self.settings.agent.rag_rerank_enabled
            per_leg = max(self.settings.agent.rag_candidates_per_leg, k) if rerank_on else k * 2

            # 1. Dense vector search
            vector_results = await self._vector_search(conn, query_embedding, service_tags, per_leg, collections)

            # 2. Sparse keyword search
            keyword_results = await self._keyword_search(conn, query, service_tags, per_leg, collections)

            candidate_ids = list(dict.fromkeys(d for d, _ in [*vector_results, *keyword_results]))
            docs_map = await self._get_documents(conn, candidate_ids)

            # 3. Local cross-encoder rerank of the union of candidates (falls back to 2-way RRF on failure)
            rerank_results: list[tuple[str, float]] = []
            if rerank_on and docs_map:
                rerank_results = self._rerank(query, docs_map)

            # 4. Reciprocal Rank Fusion over vector + BM25 (+ reranker)
            if rerank_results:
                fused = self._reciprocal_rank_fusion(
                    vector_results,
                    keyword_results,
                    rerank_results,
                    k=60,
                    weights=[1.0, 1.0, self.settings.agent.rag_rerank_weight],
                )
            else:
                fused = self._reciprocal_rank_fusion(vector_results, keyword_results, k=60)
            top_fused = fused[:k]

            references = []

            if docs_map:
                for doc_id, rrf_score in top_fused:
                    if doc_id in docs_map:
                        doc = docs_map[doc_id]
                        references.append(
                            RunbookReference(
                                runbook_id=f"runbook://{doc['source']}/{doc['doc_id']}",
                                title=doc["title"],
                                similarity_score=round(rrf_score, 4),
                                relevant_steps=self._extract_steps(doc["content"]),
                            )
                        )

            logger.info(
                "search_complete",
                query=query[:100],
                num_results=len(references),
                top_score=references[0].similarity_score if references else 0,
            )

            span.set_attribute("rag.num_results", len(references))
            if references:
                span.set_attribute("rag.top_score", references[0].similarity_score)

            latency_ms = (time.time() - start_time) * 1000
            span.set_attribute("rag.latency_ms", latency_ms)

            return references

        except Exception as e:
            logger.error("hybrid_search_failed", error=str(e))
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR))
            return []
        finally:
            await conn.close()

    def _safety_args(self) -> tuple[bool, float | None]:
        agent = self.settings.agent
        return agent.rag_exclude_untrusted, agent.rag_guard_threshold

    async def _vector_search(
        self,
        conn: asyncpg.Connection,
        query_embedding: list[float],
        service_tags: list[str] | None,
        limit: int,
        collections: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Dense vector search using pgvector cosine similarity."""
        embedding_str = str(query_embedding)
        rows = await conn.fetch(
            f"""
            SELECT doc_id, 1 - (embedding <=> $1::vector) AS similarity
            FROM documents
            WHERE ($2::text[] IS NULL OR service_tags && $2)
              AND ($3::text[] IS NULL OR collection = ANY($3))
              AND {_safety(5, 6)}
            ORDER BY embedding <=> $1::vector
            LIMIT $4
            """,  # noqa: S608 - interpolated fragments are constants with integer placeholders
            embedding_str,
            service_tags or None,
            collections or None,
            limit,
            *self._safety_args(),
        )

        return [(row["doc_id"], float(row["similarity"])) for row in rows]

    async def _keyword_search(
        self,
        conn: asyncpg.Connection,
        query: str,
        service_tags: list[str] | None,
        limit: int,
        collections: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Sparse BM25 search: ParadeDB pg_search inside Postgres, or the in-process fallback.

        'auto' tries ParadeDB first. On any Postgres error it falls back to the in-process scorer for that
        call, and stops trying ParadeDB for the rest of the process if the extension or index is missing.
        """
        backend = self.settings.agent.rag_bm25_backend
        if backend != "inprocess" and not self._paradedb_unavailable:
            try:
                return await self._keyword_search_paradedb(conn, query, service_tags, limit, collections)
            except asyncpg.PostgresError as exc:
                if backend == "paradedb":
                    raise
                missing = isinstance(exc, asyncpg.UndefinedFunctionError | asyncpg.UndefinedObjectError) or (
                    "paradedb" in str(exc).lower()
                )
                self._paradedb_unavailable = self._paradedb_unavailable or missing
                logger.warning(
                    "paradedb_bm25_failed_falling_back",
                    error=str(exc)[:200],
                    disabled_for_process=self._paradedb_unavailable,
                )
        return await self._keyword_search_inprocess(conn, query, service_tags, limit, collections)

    async def _keyword_search_paradedb(
        self,
        conn: asyncpg.Connection,
        query: str,
        service_tags: list[str] | None,
        limit: int,
        collections: list[str] | None,
    ) -> list[tuple[str, float]]:
        """BM25 ranking in the pg_search index (migration 005). The query is tokenized server-side and the
        collection filter runs inside the index scan, so no table is loaded into Python."""
        rows = await conn.fetch(
            f"""
            SELECT doc_id, pdb.score(id) AS score
            FROM documents
            WHERE (title ||| $1::text OR content ||| $1::text)
              AND ($2::text[] IS NULL OR service_tags && $2)
              AND ($3::text[] IS NULL OR collection = ANY($3))
              AND {_safety(5, 6)}
            ORDER BY pdb.score(id) DESC
            LIMIT $4
            """,  # noqa: S608
            query,
            service_tags or None,
            collections or None,
            limit,
            *self._safety_args(),
        )
        return [(row["doc_id"], float(row["score"])) for row in rows]

    async def _keyword_search_inprocess(
        self,
        conn: asyncpg.Connection,
        query: str,
        service_tags: list[str] | None,
        limit: int,
        collections: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Okapi BM25 scored in Python over the whole table (fallback; O(table) per query).

        Term statistics (IDF, average length) come from the whole table so scores do not shift with the
        filters; only eligible documents are ranked.
        """
        rows = await conn.fetch(
            f"""
            SELECT doc_id, title, content,
                   (($1::text[] IS NULL OR service_tags && $1)
                    AND ($2::text[] IS NULL OR collection = ANY($2))
                    AND {_safety(3, 4)}) AS eligible
            FROM documents
            """,  # noqa: S608
            service_tags or None,
            collections or None,
            *self._safety_args(),
        )
        corpus = BM25Corpus.build({row["doc_id"]: f"{row['title']} {row['content']}" for row in rows})
        eligible_ids = [row["doc_id"] for row in rows if row["eligible"]]
        return corpus.rank(query, eligible_ids, limit)

    def _reciprocal_rank_fusion(
        self,
        *result_lists: list[tuple[str, float]],
        k: int = 60,
        weights: list[float] | None = None,
    ) -> list[tuple[str, float]]:
        """
        Merge multiple ranked lists using Reciprocal Rank Fusion.
        RRF score = sum(weight / (k + rank)) across all lists (weights default to 1.0).
        """
        scores: dict[str, float] = {}

        for i, result_list in enumerate(result_lists):
            weight = weights[i] if weights else 1.0
            for rank, (doc_id, _) in enumerate(result_list):
                if doc_id not in scores:
                    scores[doc_id] = 0.0
                scores[doc_id] += weight / (k + rank + 1)

        # Sort by RRF score descending
        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results

    def _rerank(self, query: str, docs: dict[str, dict[str, Any]]) -> list[tuple[str, float]]:
        """Rerank candidate documents; returns [] (so callers fall back to RRF) if the reranker fails."""
        try:
            reranker = get_reranker(self.settings.agent.rag_rerank_model, self.settings.agent.rag_rerank_cache_dir)
            passages = {doc_id: f"{d['title']}\n{d['content']}" for doc_id, d in docs.items()}
            return reranker.rerank(query, passages)
        except Exception as e:
            logger.warning("rerank_failed_falling_back_to_rrf", error=str(e))
            return []

    async def _get_documents(self, conn: asyncpg.Connection, doc_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch several documents at once, keyed by doc_id."""
        if not doc_ids:
            return {}
        rows = await conn.fetch(
            "SELECT doc_id, title, source, content, metadata, service_tags FROM documents WHERE doc_id = ANY($1)",
            doc_ids,
        )
        return {row["doc_id"]: dict(row) for row in rows}

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
