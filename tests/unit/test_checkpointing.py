"""Diagnosis checkpointer: backend selection and crash-resume on Postgres."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from agents.diagnosis.checkpointing import CheckpointerProvider

DSN = os.environ.get("PARADEDB_TEST_DSN")


@pytest.fixture
def event_loop_policy() -> asyncio.AbstractEventLoopPolicy:
    # psycopg async cannot run on Windows' default Proactor loop.
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


class _S(TypedDict):
    steps: list[str]


def _graph(saver: object, fail: dict[str, bool]):  # noqa: ANN202
    def a(_: _S) -> dict:
        return {"steps": ["a"]}

    def b(s: _S) -> dict:
        if fail["b"]:
            raise RuntimeError("process died")
        return {"steps": [*s["steps"], "b"]}

    g = StateGraph(_S)
    g.add_node("a", a)
    g.add_node("b", b)
    g.set_entry_point("a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile(checkpointer=saver)


@pytest.mark.asyncio
async def test_memory_backend_is_default_and_shared() -> None:
    provider = CheckpointerProvider(backend="memory")
    first = await provider.get()
    assert isinstance(first, MemorySaver)
    assert await provider.get() is first


@pytest.mark.asyncio
@pytest.mark.skipif(not DSN, reason="PARADEDB_TEST_DSN not set")
async def test_postgres_resumes_after_crash() -> None:
    config = {"configurable": {"thread_id": f"ckpt-test-{uuid.uuid4().hex}"}}
    fail = {"b": True}

    p1 = CheckpointerProvider(backend="postgres", dsn=DSN)
    try:
        with pytest.raises(RuntimeError):
            await _graph(await p1.get(), fail).ainvoke({"steps": []}, config=config)
    finally:
        await p1.close()

    # A fresh provider stands in for a restarted process.
    fail["b"] = False
    p2 = CheckpointerProvider(backend="postgres", dsn=DSN)
    try:
        app = _graph(await p2.get(), fail)
        snapshot = await app.aget_state(config)
        assert snapshot.next == ("b",)
        result = await app.ainvoke(None, config=config)
        assert result["steps"] == ["a", "b"]
    finally:
        await p2.close()
