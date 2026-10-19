"""Offline A/B of retrieval strategies over the sample runbooks, scored with DeepEval.

Strategies (same candidate pool: top-N from the vector leg and the BM25 leg, deduplicated):
  rrf_only       - RRF(vector, BM25)
  fusion_3way    - weighted RRF(vector, BM25, FlashRank)   <- production path in HybridSearchService
  reranker_only  - FlashRank order over the candidate pool

Runs in memory (no Postgres). The vector leg needs an embedding model: `--embeddings real` uses the
configured endpoint; `--embeddings hashing` uses a lexical hashing stub (CI only, not a semantic model).

Usage: python -m evals.retrieval_ab [--embeddings real|hashing] [--k 5] [--per-leg 50] [--rerank-weight 1.0]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")

from deepeval.metrics import BaseMetric  # noqa: E402
from deepeval.test_case import LLMTestCase  # noqa: E402
from langchain_text_splitters import RecursiveCharacterTextSplitter  # noqa: E402

from knowledge_base.ingestion.pipeline import RunbookIngestionPipeline  # noqa: E402
from knowledge_base.retrieval.bm25 import BM25Corpus, tokenize  # noqa: E402
from knowledge_base.retrieval.rerank import FlashRankReranker  # noqa: E402
from knowledge_base.retrieval.search import HybridSearchService  # noqa: E402

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings

QUERIES_PATH = Path(__file__).parent / "data" / "retrieval_queries.json"
STRATEGIES = ("rrf_only", "fusion_3way", "reranker_only")
RRF_K = 60


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    title: str
    text: str


class HashingEmbeddings:
    """Deterministic bag-of-words hashing embedder for tests. Lexical, so it is NOT a semantic baseline."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in tokenize(text):
            v[sum(ord(c) * 31**i for i, c in enumerate(tok)) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    async def aembed_query(self, text: str) -> list[float]:
        return self._vec(text)


def load_chunks() -> list[Chunk]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=512, chunk_overlap=64, length_function=len, separators=["\n\n", "\n", ". ", " ", ""]
    )
    pipeline = RunbookIngestionPipeline.__new__(RunbookIngestionPipeline)
    chunks: list[Chunk] = []
    for rb in pipeline._get_sample_runbooks():
        for i, text in enumerate(splitter.split_text(rb["content"])):
            chunks.append(Chunk(f"{rb['title']}::{i}", rb["title"], text))
    return chunks


def load_queries() -> list[dict]:
    return json.loads(QUERIES_PATH.read_text(encoding="utf-8"))["queries"]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    return dot / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1.0)


class RetrievalLab:
    def __init__(
        self,
        chunks: list[Chunk],
        embeddings: Embeddings | HashingEmbeddings,
        reranker: FlashRankReranker,
        per_leg: int = 50,
        rerank_weight: float = 1.0,
    ) -> None:
        self.chunks = {c.chunk_id: c for c in chunks}
        self.embeddings = embeddings
        self.reranker = reranker
        self.per_leg = per_leg
        self.rerank_weight = rerank_weight
        self.bm25 = BM25Corpus.build({c.chunk_id: f"{c.title} {c.text}" for c in chunks})
        self.doc_vectors: dict[str, list[float]] = {}
        self._fuse = HybridSearchService.__new__(HybridSearchService)._reciprocal_rank_fusion

    async def index(self) -> None:
        ids = list(self.chunks)
        vecs = await self.embeddings.aembed_documents([f"{self.chunks[i].title}\n{self.chunks[i].text}" for i in ids])
        self.doc_vectors = dict(zip(ids, vecs, strict=True))

    async def retrieve(self, strategy: str, query: str, k: int) -> list[str]:
        """Return top-k runbook titles (deduplicated, rank order) for the strategy."""
        qv = await self.embeddings.aembed_query(query)
        vector = sorted(((i, _cosine(qv, v)) for i, v in self.doc_vectors.items()), key=lambda x: -x[1])[: self.per_leg]
        keyword = self.bm25.rank(query, list(self.chunks), self.per_leg)
        pool = list(dict.fromkeys(i for i, _ in [*vector, *keyword]))
        rerank = self.reranker.rerank(query, {i: f"{self.chunks[i].title}\n{self.chunks[i].text}" for i in pool})

        if strategy == "rrf_only":
            ranked = self._fuse(vector, keyword, k=RRF_K)
        elif strategy == "fusion_3way":
            ranked = self._fuse(vector, keyword, rerank, k=RRF_K, weights=[1.0, 1.0, self.rerank_weight])
        elif strategy == "reranker_only":
            ranked = rerank
        else:
            raise ValueError(strategy)
        return list(dict.fromkeys(self.chunks[i].title for i, _ in ranked))[:k]


class RetrievalRecallMetric(BaseMetric):
    """Deterministic (no LLM judge): fraction of expected runbooks present in the retrieved context."""

    def __init__(self, threshold: float = 1.0) -> None:
        self.threshold = threshold
        self.async_mode = False
        self.include_reason = True

    def measure(self, test_case: LLMTestCase, *args: object, **kwargs: object) -> float:
        expected = set(test_case.expected_output.split("|")) if test_case.expected_output else set()
        retrieved = test_case.retrieval_context or []
        hits = expected & set(retrieved)
        self.score = len(hits) / len(expected) if expected else 0.0
        self.success = self.score >= self.threshold
        self.reason = f"found {sorted(hits)} of {sorted(expected)} in {retrieved}"
        return self.score

    async def a_measure(self, test_case: LLMTestCase, *args: object, **kwargs: object) -> float:
        return self.measure(test_case)

    def is_successful(self) -> bool:
        return bool(self.success)

    @property
    def __name__(self) -> str:  # type: ignore[override]
        return "Retrieval Recall"


async def evaluate_strategies(lab: RetrievalLab, queries: list[dict], k: int) -> dict[str, dict[str, float]]:
    """Per strategy: recall@k, hit@k and MRR, with recall computed through the DeepEval metric."""
    out: dict[str, dict[str, float]] = {}
    for strategy in STRATEGIES:
        recalls: list[float] = []
        hits: list[float] = []
        rr: list[float] = []
        for q in queries:
            retrieved = await lab.retrieve(strategy, q["query"], k)
            case = LLMTestCase(
                input=q["query"],
                actual_output="",
                expected_output="|".join(q["relevant"]),
                retrieval_context=retrieved,
            )
            metric = RetrievalRecallMetric()
            recalls.append(metric.measure(case))
            hits.append(1.0 if any(t in q["relevant"] for t in retrieved) else 0.0)
            rank = next((i + 1 for i, t in enumerate(retrieved) if t in q["relevant"]), None)
            rr.append(1.0 / rank if rank else 0.0)
        n = len(queries)
        out[strategy] = {f"recall@{k}": sum(recalls) / n, f"hit@{k}": sum(hits) / n, "mrr": sum(rr) / n}
    return out


async def run(embeddings_mode: str, k: int, per_leg: int, rerank_weight: float) -> dict[str, dict[str, float]]:
    from shared.config import get_settings
    from shared.llm import get_embeddings

    cfg = get_settings().agent
    embeddings = HashingEmbeddings() if embeddings_mode == "hashing" else get_embeddings()
    lab = RetrievalLab(
        load_chunks(),
        embeddings,
        FlashRankReranker(cfg.rag_rerank_model, cfg.rag_rerank_cache_dir),
        per_leg,
        rerank_weight,
    )
    await lab.index()
    return await evaluate_strategies(lab, load_queries(), k)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embeddings", choices=["real", "hashing"], default="real")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--per-leg", type=int, default=50)
    ap.add_argument("--rerank-weight", type=float, default=1.0)
    ap.add_argument("--out", default=str(Path(__file__).parent / "reports" / "retrieval_ab.json"))
    a = ap.parse_args()
    results = asyncio.run(run(a.embeddings, a.k, a.per_leg, a.rerank_weight))
    for name, m in results.items():
        print(f"{name:14s} " + "  ".join(f"{key}={val:.3f}" for key, val in m.items()))
    Path(a.out).write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
