"""Retrieval-time poisoning controls: trust-tier exclusion and the stored guard_score ceiling."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from knowledge_base.retrieval.search import HybridSearchService


class RecordingConn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.calls.append((sql, args))
        return []


def _svc(exclude: bool = True, threshold: float | None = 0.5, backend: str = "paradedb") -> HybridSearchService:
    svc = HybridSearchService.__new__(HybridSearchService)
    svc.settings = SimpleNamespace(  # type: ignore[assignment]
        agent=SimpleNamespace(rag_bm25_backend=backend, rag_exclude_untrusted=exclude, rag_guard_threshold=threshold)
    )
    svc._paradedb_unavailable = False
    return svc


@pytest.mark.asyncio
async def test_vector_query_applies_safety_filter_and_args() -> None:
    conn = RecordingConn()
    await _svc()._vector_search(conn, [0.1], None, 7, ["database"])  # type: ignore[arg-type]
    sql, args = conn.calls[0]
    assert "trust_tier" in sql and "guard_score" in sql
    assert args[3:] == (7, True, 0.5)


@pytest.mark.asyncio
async def test_paradedb_query_applies_safety_filter_and_args() -> None:
    conn = RecordingConn()
    await _svc()._keyword_search(conn, "lag", None, 7, None)  # type: ignore[arg-type]
    sql, args = conn.calls[0]
    assert "pdb.score" in sql and "trust_tier" in sql and "guard_score" in sql
    assert args[-2:] == (True, 0.5)


@pytest.mark.asyncio
async def test_inprocess_query_applies_safety_filter_and_args() -> None:
    conn = RecordingConn()
    await _svc(backend="inprocess")._keyword_search(conn, "lag", None, 7, None)  # type: ignore[arg-type]
    sql, args = conn.calls[0]
    assert "trust_tier" in sql and "guard_score" in sql
    assert args == (None, None, True, 0.5)


@pytest.mark.asyncio
async def test_safety_can_be_disabled_via_settings() -> None:
    conn = RecordingConn()
    await _svc(exclude=False, threshold=None)._vector_search(conn, [0.1], None, 5)  # type: ignore[arg-type]
    assert conn.calls[0][1][-2:] == (False, None)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("PARADEDB_TEST_DSN"), reason="set PARADEDB_TEST_DSN to a ParadeDB with migrations 001-006"
)
async def test_unsafe_documents_never_returned_by_any_leg() -> None:
    conn = await asyncpg.connect(os.environ["PARADEDB_TEST_DSN"], statement_cache_size=0)
    rows = [
        ("sf_ok", "trusted-external", None),
        ("sf_unscored", "synthetic", None),
        ("sf_untrusted", "untrusted", None),
        ("sf_flagged", "synthetic", 0.97),
        ("sf_low", "synthetic", 0.2),
    ]
    try:
        await conn.execute("DELETE FROM documents WHERE doc_id LIKE 'sf_%'")
        for doc_id, tier, score in rows:
            await conn.execute(
                """INSERT INTO documents (doc_id, title, source, content, collection, metadata, guard_score, embedding)
                   VALUES ($1, 'zebracorn failover', 't', 'zebracorn failover standby promote', 'database',
                           jsonb_build_object('trust_tier', $2::text), $3::real,
                           array_fill(0.05::real, ARRAY[768])::vector)""",
                doc_id,
                tier,
                score,
            )
        expected = {"sf_ok", "sf_unscored", "sf_low"}
        query_vec = [0.05] * 768
        for backend in ("paradedb", "inprocess"):
            kw = await _svc(backend=backend)._keyword_search(conn, "zebracorn failover", None, 50, None)
            assert {d for d, _ in kw if d.startswith("sf_")} == expected, backend
        vec = await _svc()._vector_search(conn, query_vec, None, 500, None)
        assert {d for d, _ in vec if d.startswith("sf_")} == expected

        off = await _svc(exclude=False, threshold=None)._vector_search(conn, query_vec, None, 500, None)
        assert {d for d, _ in off if d.startswith("sf_")} == {r[0] for r in rows}
    finally:
        await conn.execute("DELETE FROM documents WHERE doc_id LIKE 'sf_%'")
        await conn.close()
