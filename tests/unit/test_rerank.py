from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

import knowledge_base.retrieval.search as search_mod
from knowledge_base.retrieval.rerank import FlashRankReranker
from knowledge_base.retrieval.search import HybridSearchService

if TYPE_CHECKING:
    import pytest


def _service() -> HybridSearchService:
    svc = HybridSearchService.__new__(HybridSearchService)
    svc.settings = SimpleNamespace(agent=SimpleNamespace(rag_rerank_model="m", rag_rerank_cache_dir="c"))
    return svc


DOCS = {"a": {"title": "A", "content": "alpha"}, "b": {"title": "B", "content": "beta"}}


def test_weighted_rrf_lets_reranker_dominate() -> None:
    svc = _service()
    vec = [("a", 0.9), ("b", 0.8)]
    kw = [("a", 3.0), ("b", 2.0)]
    rr = [("b", 1.0), ("a", 0.1)]
    assert svc._reciprocal_rank_fusion(vec, kw, rr)[0][0] == "a"
    assert svc._reciprocal_rank_fusion(vec, kw, rr, weights=[1.0, 1.0, 5.0])[0][0] == "b"


def test_rerank_returns_reranker_order(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(rerank=lambda q, p: [("b", 0.9), ("a", 0.1)])
    monkeypatch.setattr(search_mod, "get_reranker", lambda *_: fake)
    assert _service()._rerank("q", DOCS) == [("b", 0.9), ("a", 0.1)]


def test_rerank_failure_falls_back_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object) -> None:
        raise RuntimeError("model missing")

    monkeypatch.setattr(search_mod, "get_reranker", boom)
    assert _service()._rerank("q", DOCS) == []


def test_flashrank_reranker_maps_results(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeRanker:
        def rerank(self, request: SimpleNamespace) -> list[dict[str, object]]:
            return [{"id": p["id"], "text": p["text"], "score": 1.0 / (i + 1)} for i, p in enumerate(request.passages)]

    r = FlashRankReranker("m", "c")
    r._ranker = FakeRanker()
    assert r.rerank("q", {"a": "x", "b": "y"}) == [("a", 1.0), ("b", 0.5)]
    assert r.rerank("q", {}) == []
