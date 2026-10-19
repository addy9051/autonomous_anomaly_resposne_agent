"""Unit tests for the BM25 keyword ranker."""

from __future__ import annotations

from knowledge_base.retrieval.bm25 import BM25Corpus, tokenize

DOCS = {
    "db": "Database deadlock runbook: kill long running queries and check the connection pool.",
    "net": "Network packet loss runbook: check DNS, load balancer health and TLS handshake errors.",
    "app": "Application latency runbook: check recent deployment, payment-gateway p99_latency and rollback.",
}


def test_tokenize_splits_identifiers_and_drops_stopwords() -> None:
    assert tokenize("The payment-gateway p99_latency is HIGH") == ["payment", "gateway", "p99", "latency", "high"]


def test_matching_document_ranks_first() -> None:
    corpus = BM25Corpus.build(DOCS)
    ranked = corpus.rank("deadlock in the connection pool", list(DOCS), limit=3)
    assert ranked[0][0] == "db"
    assert all(score > 0 for _, score in ranked)


def test_rare_term_outweighs_common_term() -> None:
    corpus = BM25Corpus.build(DOCS)
    # "runbook" is in every document; "deadlock" is in one.
    assert corpus.idf("deadlock") > corpus.idf("runbook") > 0


def test_no_overlap_returns_nothing() -> None:
    corpus = BM25Corpus.build(DOCS)
    assert corpus.rank("kubernetes zebra", list(DOCS), limit=3) == []


def test_candidate_filter_and_limit_are_respected() -> None:
    corpus = BM25Corpus.build(DOCS)
    assert {d for d, _ in corpus.rank("runbook check", ["net", "app"], limit=3)} <= {"net", "app"}
    ranked = corpus.rank("runbook check", ["net", "app"], limit=1)
    assert len(ranked) == 1
    assert ranked[0][0] in {"net", "app"}


def test_length_normalisation_prefers_shorter_document() -> None:
    corpus = BM25Corpus.build({"short": "timeout error", "long": "timeout error " + "filler " * 200})
    assert corpus.score("timeout", "short") > corpus.score("timeout", "long")


def test_empty_corpus_is_safe() -> None:
    corpus = BM25Corpus.build({})
    assert corpus.rank("anything", [], limit=5) == []
