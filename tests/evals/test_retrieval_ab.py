"""Retrieval A/B (RRF-only vs 3-way fusion vs reranker-only) scored with DeepEval, run offline."""

from __future__ import annotations

from pathlib import Path

import pytest
from deepeval.test_case import LLMTestCase

from evals.retrieval_ab import (
    STRATEGIES,
    HashingEmbeddings,
    RetrievalLab,
    RetrievalRecallMetric,
    evaluate_strategies,
    load_chunks,
    load_queries,
)
from knowledge_base.retrieval.rerank import FlashRankReranker
from shared.config import get_settings

pytestmark = pytest.mark.skipif(
    not (Path(get_settings().agent.rag_rerank_cache_dir) / get_settings().agent.rag_rerank_model).exists(),
    reason="FlashRank model not provisioned in rag_rerank_cache_dir",
)


def test_recall_metric_scores_partial_and_full_hits() -> None:
    case = LLMTestCase(input="q", actual_output="", expected_output="A|B", retrieval_context=["A", "C"])
    metric = RetrievalRecallMetric()
    assert metric.measure(case) == 0.5
    assert not metric.is_successful()
    case.retrieval_context = ["B", "A"]
    assert metric.measure(case) == 1.0
    assert metric.is_successful()


@pytest.mark.asyncio
async def test_strategies_retrieve_expected_runbooks() -> None:
    cfg = get_settings().agent
    lab = RetrievalLab(
        load_chunks(),
        HashingEmbeddings(),
        FlashRankReranker(cfg.rag_rerank_model, cfg.rag_rerank_cache_dir),
    )
    await lab.index()
    results = await evaluate_strategies(lab, load_queries(), k=5)
    assert set(results) == set(STRATEGIES)
    for strategy, metrics in results.items():
        assert metrics["hit@5"] >= 0.9, f"{strategy}: {metrics}"
