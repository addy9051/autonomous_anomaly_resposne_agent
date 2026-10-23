"""
Async Database Connection Pool & Query Executor.
Compatible with Neon Serverless Postgres, Supabase, AWS RDS, and Render Postgres.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

import ssl
from urllib.parse import urlparse, urlunparse

from core.config import get_settings
from core.utils import get_logger

logger = get_logger("database")

_pool: asyncpg.Pool | None = None


def _prepare_dsn(dsn: str) -> tuple[str, ssl.SSLContext | bool]:
    """Strip unsupported query params for asyncpg and configure SSL for Neon."""
    parsed = urlparse(dsn)
    clean_dsn = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    if "neon.tech" in dsn or "sslmode=require" in dsn or "supabase.co" in dsn or "render.com" in dsn:
        ctx = ssl.create_default_context()
        return clean_dsn, ctx
    return clean_dsn, False


async def init_db_pool() -> asyncpg.Pool:
    """Initialize the asyncpg connection pool with SSL support for cloud Postgres."""
    global _pool
    if _pool is not None:
        return _pool

    settings = get_settings()
    clean_dsn, ssl_ctx = _prepare_dsn(settings.database_url)

    logger.info("db_pool_connecting", ssl=bool(ssl_ctx), neon_branch=settings.neon_branch)
    try:
        _pool = await asyncpg.create_pool(
            dsn=clean_dsn,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            command_timeout=30.0,
            ssl=ssl_ctx,
        )
        logger.info("db_pool_initialized")
        return _pool
    except Exception as exc:
        logger.error("db_pool_init_failed", error=str(exc))
        raise


async def close_db_pool() -> None:
    """Close the asyncpg connection pool cleanly on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("db_pool_closed")


@asynccontextmanager
async def get_db_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """Provide a connection from the pool."""
    global _pool
    if _pool is None:
        await init_db_pool()
    assert _pool is not None
    async with _pool.acquire() as conn:
        yield conn


async def fetch_one(query: str, *args: Any) -> dict[str, Any] | None:
    """Fetch single row as dictionary."""
    async with get_db_connection() as conn:
        row = await conn.fetchrow(query, *args)
        return dict(row) if row else None


async def fetch_all(query: str, *args: Any) -> list[dict[str, Any]]:
    """Fetch multiple rows as list of dictionaries."""
    async with get_db_connection() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]


async def execute(query: str, *args: Any) -> str:
    """Execute non-returning query (INSERT, UPDATE, DELETE)."""
    async with get_db_connection() as conn:
        return await conn.execute(query, *args)
