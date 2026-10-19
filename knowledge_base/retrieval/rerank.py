"""Local cross-encoder reranking with FlashRank (ONNX, CPU, no runtime network calls)."""

from __future__ import annotations

from typing import Any, Protocol

from shared.utils import get_logger

logger = get_logger("reranker")


class Reranker(Protocol):
    def rerank(self, query: str, passages: dict[str, str]) -> list[tuple[str, float]]: ...


class FlashRankReranker:
    """Scores (query, passage) pairs jointly and returns doc ids ordered best-first.

    The model is loaded lazily. FlashRank downloads weights only if they are missing from cache_dir,
    so in the egress-restricted sandbox the model must be pre-provisioned there.
    """

    def __init__(self, model_name: str, cache_dir: str, max_length: int = 512) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.max_length = max_length
        self._ranker: Any = None

    def _load(self) -> Any:  # noqa: ANN401
        if self._ranker is None:
            from flashrank import Ranker

            self._ranker = Ranker(model_name=self.model_name, cache_dir=self.cache_dir, max_length=self.max_length)
        return self._ranker

    def rerank(self, query: str, passages: dict[str, str]) -> list[tuple[str, float]]:
        if not passages:
            return []
        from flashrank import RerankRequest

        request = RerankRequest(
            query=query,
            passages=[{"id": doc_id, "text": text} for doc_id, text in passages.items()],
        )
        results = self._load().rerank(request)
        return [(str(r["id"]), float(r["score"])) for r in results]


_reranker: FlashRankReranker | None = None


def get_reranker(model_name: str, cache_dir: str) -> FlashRankReranker:
    global _reranker
    if _reranker is None or (_reranker.model_name, _reranker.cache_dir) != (model_name, cache_dir):
        _reranker = FlashRankReranker(model_name, cache_dir)
    return _reranker
