"""
Common utilities used across all agents and modules.

Provides:
- Structured logging setup (structlog)
- OpenTelemetry tracing helpers
- Redis client factory
- PostgreSQL connection factory
- LLM cost tracking middleware
- Retry decorators for tool calls
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable  # noqa: TC003
from typing import Any

import redis.asyncio as aioredis
import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.config import get_settings

# ─── Structured Logging ──────────────────────────────────────────


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structlog for consistent JSON logging."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if get_settings().app.app_env == "development"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a named structlog logger."""
    return structlog.get_logger(name)


# ─── OpenTelemetry Tracing ───────────────────────────────────────


def setup_tracing() -> trace.Tracer:
    """Initialize OpenTelemetry tracing with OTLP exporter."""
    settings = get_settings()
    resource = Resource.create({
        "service.name": settings.observability.otel_service_name,
        "deployment.environment": settings.app.app_env,
    })
    provider = TracerProvider(resource=resource)

    if settings.observability.otel_exporter_otlp_endpoint:
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.observability.otel_exporter_otlp_endpoint,
            insecure=True,
        )
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    trace.set_tracer_provider(provider)
    return trace.get_tracer(settings.observability.otel_service_name)


def get_tracer() -> trace.Tracer:
    """Get the global tracer instance."""
    settings = get_settings()
    return trace.get_tracer(settings.observability.otel_service_name)


# ─── Langfuse Observability ──────────────────────────────────────


def get_langfuse_callbacks(session_id: str | None = None) -> list[Any]:
    """
    Get a pre-configured list containing the Langfuse CallbackHandler.
    Returns an empty list if Langfuse is disabled or not fully configured.
    """
    settings = get_settings()
    if not settings.observability.langfuse_enabled:
        return []

    if not (settings.observability.langfuse_secret_key and settings.observability.langfuse_public_key):
        return []

    try:
        from langfuse.callback import CallbackHandler
        
        handler = CallbackHandler(
            public_key=settings.observability.langfuse_public_key,
            secret_key=settings.observability.langfuse_secret_key,
            host=settings.observability.langfuse_host,
            session_id=session_id
        )
        return [handler]
    except ImportError:
        logger = get_logger("langfuse_setup")
        logger.warning("Langfuse package missing. Cannot initialize callbacks.")
        return []

# ─── Redis Client ────────────────────────────────────────────────


async def get_redis_client() -> aioredis.Redis:
    """Create an async Redis client."""
    settings = get_settings()
    return aioredis.from_url(
        settings.data.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )


# ─── PostgreSQL Client ──────────────────────────────────────────


def get_async_engine() -> Any:  # noqa: ANN401
    """Create SQLAlchemy async engine for PostgreSQL."""
    settings = get_settings()
    return create_async_engine(
        settings.data.async_postgres_dsn,
        echo=settings.app.app_env == "development",
        pool_size=10,
        max_overflow=20,
    )


def get_async_session_factory() -> Any:  # noqa: ANN401
    """Create an async session factory."""
    engine = get_async_engine()
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# ─── LLM Cost Tracker ───────────────────────────────────────────


class LLMCostTracker:
    """
    Track LLM token usage and cost per incident.
    Used to enforce the 50K token budget per incident.
    """

    # Approximate cost per 1K tokens (USD)
    COST_PER_1K = {
        "gpt-4o": {"input": 0.005, "output": 0.015},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
        "claude-3-5-haiku-20241022": {"input": 0.001, "output": 0.005},
    }

    def __init__(self, incident_id: str, max_tokens: int = 50_000) -> None:
        self.incident_id = incident_id
        self.max_tokens = max_tokens
        self.total_tokens = 0
        self.total_cost = 0.0
        self.calls: list[dict[str, Any]] = []

    def track(self, model: str, input_tokens: int, output_tokens: int) -> None:
        """Record a single LLM call."""
        total = input_tokens + output_tokens
        self.total_tokens += total

        cost_rates = self.COST_PER_1K.get(model, {"input": 0.01, "output": 0.03})
        cost = (input_tokens / 1000 * cost_rates["input"]) + (
            output_tokens / 1000 * cost_rates["output"]
        )
        self.total_cost += cost

        self.calls.append({
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
            "cumulative_tokens": self.total_tokens,
        })

    @property
    def budget_remaining(self) -> int:
        return max(0, self.max_tokens - self.total_tokens)

    @property
    def budget_exceeded(self) -> bool:
        return self.total_tokens >= self.max_tokens


# ─── Retry Decorator for Tool Calls ─────────────────────────────


def retry_tool_call(max_attempts: int = 3) -> Callable[..., Any]:
    """Retry decorator for external tool calls with exponential backoff."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )


# ─── Timer Context Manager ──────────────────────────────────────


class Timer:
    """Simple context manager to time operations."""

    def __init__(self) -> None:
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: object) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000
