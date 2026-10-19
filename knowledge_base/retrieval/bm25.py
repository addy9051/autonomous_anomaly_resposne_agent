"""Okapi BM25 scoring for the sparse leg of hybrid retrieval."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Natural-language queries are long; dropping function words keeps them from dominating the match.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
    ]
)


def tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokens, so `payment-gateway` and `p99_latency` split into their parts."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


@dataclass(frozen=True)
class BM25Corpus:
    """Term statistics for one corpus; build once, then score any number of queries."""

    term_freqs: dict[str, Counter[str]]
    doc_lengths: dict[str, int]
    doc_freqs: Counter[str]
    avg_doc_length: float

    @classmethod
    def build(cls, documents: dict[str, str]) -> BM25Corpus:
        term_freqs = {doc_id: Counter(tokenize(text)) for doc_id, text in documents.items()}
        doc_lengths = {doc_id: sum(tf.values()) for doc_id, tf in term_freqs.items()}
        doc_freqs: Counter[str] = Counter()
        for tf in term_freqs.values():
            doc_freqs.update(tf.keys())
        avg = sum(doc_lengths.values()) / len(doc_lengths) if doc_lengths else 0.0
        return cls(term_freqs, doc_lengths, doc_freqs, avg)

    def idf(self, term: str) -> float:
        """Lucene-style IDF, which stays non-negative for terms present in most documents."""
        n = len(self.term_freqs)
        df = self.doc_freqs.get(term, 0)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def score(self, query: str, doc_id: str, k1: float = 1.5, b: float = 0.75) -> float:
        tf = self.term_freqs.get(doc_id)
        if not tf or self.avg_doc_length == 0:
            return 0.0
        length_norm = 1.0 - b + b * self.doc_lengths[doc_id] / self.avg_doc_length
        total = 0.0
        for term in set(tokenize(query)):
            freq = tf.get(term, 0)
            if freq:
                total += self.idf(term) * freq * (k1 + 1.0) / (freq + k1 * length_norm)
        return total

    def rank(self, query: str, candidate_ids: list[str], limit: int) -> list[tuple[str, float]]:
        """Candidates with a positive BM25 score, best first; ties break on doc_id for determinism."""
        scored = [(doc_id, self.score(query, doc_id)) for doc_id in candidate_ids]
        scored = [item for item in scored if item[1] > 0.0]
        scored.sort(key=lambda item: (-item[1], item[0]))
        return scored[:limit]
