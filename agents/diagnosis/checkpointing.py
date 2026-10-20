"""Checkpointer factory for the diagnosis graph.

``memory`` keeps state in-process (tests, local dev). ``postgres`` stores every
step in Postgres, so a restarted API can resume a half-finished diagnosis.
A configured Postgres backend that cannot connect raises; it never silently
falls back to memory.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from langgraph.checkpoint.memory import MemorySaver

from shared.config import get_settings
from shared.utils import get_logger

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = get_logger(__name__)


class CheckpointerProvider:
    """Lazily builds one saver per event loop and reuses it across incidents."""

    def __init__(self, backend: str | None = None, dsn: str | None = None) -> None:
        settings = get_settings()
        self.backend = backend or settings.agent.checkpointer_backend
        self.dsn = dsn or settings.data.postgres_dsn
        self._memory = MemorySaver()
        self._postgres: BaseCheckpointSaver | None = None
        self._pool: object | None = None
        self._lock: asyncio.Lock | None = None

    async def get(self) -> BaseCheckpointSaver:
        if self.backend != "postgres":
            return self._memory
        if self._postgres is not None:
            return self._postgres
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self._postgres is None:
                self._postgres = await self._open_postgres()
        return self._postgres

    async def _open_postgres(self) -> BaseCheckpointSaver:
        if sys.platform == "win32" and type(asyncio.get_running_loop()).__name__ == "ProactorEventLoop":
            raise RuntimeError(
                "Postgres checkpointer needs a selector event loop on Windows; "
                "use asyncio.WindowsSelectorEventLoopPolicy() or run on Linux."
            )
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            self.dsn,
            min_size=1,
            max_size=5,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await pool.open(wait=True, timeout=10)
        saver = AsyncPostgresSaver(pool)
        await saver.setup()
        self._pool = pool
        logger.info("diagnosis_checkpointer_ready", backend="postgres")
        return saver

    async def close(self) -> None:
        pool, self._pool, self._postgres = self._pool, None, None
        if pool is not None:
            await pool.close()  # type: ignore[attr-defined]
