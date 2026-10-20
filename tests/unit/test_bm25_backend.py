"""Tests for BM25 backend routing in HybridSearchService (ParadeDB vs in-process fallback)."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from knowledge_base.retrieval.search import HybridSearchService


class FakeConn:
    """Records SQL; the ParadeDB query raises `error` when set, the in-process table scan returns `rows`."""

    def __init__(self, error: Exception | None = None, rows: list[dict[str, Any]] | None = None) -> None:
        self.error = error
        self.rows = rows or []
        self.queries: list[str] = []

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.queries.append(sql)
        if "pdb.score" in sql:
            if self.error:
                raise self.error
            return [{"doc_id": "pg1", "score": 2.5}]
        return self.rows


def _svc(backend: str) -> HybridSearchService:
    svc = HybridSearchService.__new__(HybridSearchService)
    svc.settings = SimpleNamespace(agent=SimpleNamespace(rag_bm25_backend=backend))  # type: ignore[assignment]
    svc._paradedb_unavailable = False
    return svc


ROWS = [{"doc_id": "py1", "title": "Replication lag", "content": "replica lag standby", "eligible": True}]


@pytest.mark.asyncio
async def test_auto_uses_paradedb_when_available() -> None:
    conn = FakeConn()
    got = await _svc("auto")._keyword_search(conn, "replication lag", None, 5, ["database"])  # type: ignore[arg-type]
    assert got == [("pg1", 2.5)]
    assert len(conn.queries) == 1


@pytest.mark.asyncio
async def test_auto_falls_back_and_remembers_when_extension_missing() -> None:
    conn = FakeConn(error=asyncpg.UndefinedFunctionError("function pdb.score does not exist"), rows=ROWS)
    svc = _svc("auto")
    got = await svc._keyword_search(conn, "replica lag", None, 5, None)  # type: ignore[arg-type]
    assert [d for d, _ in got] == ["py1"]
    assert svc._paradedb_unavailable is True
    conn.queries.clear()
    await svc._keyword_search(conn, "replica lag", None, 5, None)  # type: ignore[arg-type]
    assert not any("pdb.score" in q for q in conn.queries)


@pytest.mark.asyncio
async def test_auto_missing_index_error_disables_paradedb() -> None:
    err = asyncpg.InternalServerError("`documents` does not contain a `USING paradedb` index")
    svc = _svc("auto")
    await svc._keyword_search(FakeConn(error=err, rows=ROWS), "replica", None, 5, None)  # type: ignore[arg-type]
    assert svc._paradedb_unavailable is True


@pytest.mark.asyncio
async def test_auto_transient_error_falls_back_without_disabling() -> None:
    svc = _svc("auto")
    conn = FakeConn(error=asyncpg.QueryCanceledError("canceling statement due to statement timeout"), rows=ROWS)
    got = await svc._keyword_search(conn, "replica lag", None, 5, None)  # type: ignore[arg-type]
    assert [d for d, _ in got] == ["py1"]
    assert svc._paradedb_unavailable is False


@pytest.mark.asyncio
async def test_paradedb_mode_does_not_hide_errors() -> None:
    conn = FakeConn(error=asyncpg.UndefinedFunctionError("missing"), rows=ROWS)
    with pytest.raises(asyncpg.UndefinedFunctionError):
        await _svc("paradedb")._keyword_search(conn, "x", None, 5, None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_inprocess_mode_never_queries_paradedb() -> None:
    conn = FakeConn(rows=ROWS)
    await _svc("inprocess")._keyword_search(conn, "replica lag", None, 5, None)  # type: ignore[arg-type]
    assert not any("pdb.score" in q for q in conn.queries)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.getenv("PARADEDB_TEST_DSN"), reason="set PARADEDB_TEST_DSN to a ParadeDB with migrations 001-005"
)
async def test_paradedb_integration_filters_and_ranks() -> None:
    conn = await asyncpg.connect(os.environ["PARADEDB_TEST_DSN"], statement_cache_size=0)
    try:
        await conn.execute("DELETE FROM documents WHERE doc_id LIKE 'it_%'")
        await conn.execute(
            """INSERT INTO documents (doc_id, title, source, content, collection, service_tags) VALUES
               ('it_a','Replication lag','t','check pg_stat_replication replay lag on standby',
                'database','{postgres}'),
               ('it_b','Ingress 502','t','nginx 502 bad gateway upstream pods crash','network','{nginx}')"""
        )
        svc = _svc("paradedb")
        hits = await svc._keyword_search(conn, "replication lag standby", None, 5, None)
        assert hits and "it_a" in [d for d, _ in hits[:3]]
        network_hits = await svc._keyword_search(conn, "replication lag standby", None, 5, ["network"])
        assert "it_a" not in [d for d, _ in network_hits]
        assert (
            await svc._keyword_search(conn, 'p99 > 2s AND "x" (y); DROP TABLE documents;--', None, 5, None) is not None
        )
        assert await svc._keyword_search(conn, "", None, 5, None) == []
    finally:
        await conn.execute("DELETE FROM documents WHERE doc_id LIKE 'it_%'")
        await conn.close()
