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
from contextlib import AbstractContextManager, nullcontext
from typing import Any

import redis.asyncio as aioredis
import structlog
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.config import get_settings

# ─── Structured Logging ──────────────────────────────────────────


def _add_trace_ids(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:  # noqa: ANN401
    """Attach the active OpenTelemetry trace/span id so logs join to API traces."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structlog for consistent JSON logging."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _add_trace_ids,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer()
            if get_settings().app.app_env == "development"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, log_level.upper(), logging.INFO)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a named structlog logger."""
    return structlog.get_logger(name)


# ─── OpenTelemetry Tracing ───────────────────────────────────────


def setup_tracing(app: Any | None = None) -> trace.Tracer:  # noqa: ANN401
    """
    Initialize OpenTelemetry tracing and, when given a FastAPI app, instrument its requests.

    Spans are exported only when OTEL_EXPORTER_OTLP_ENDPOINT is set and passes the egress policy
    (in-VPC collector, e.g. ADOT). Otherwise spans are created in-process (so trace ids still exist
    for logs) but are not exported.
    """
    settings = get_settings()
    logger = get_logger("tracing_setup")

    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        resource = Resource.create(
            {
                "service.name": settings.observability.otel_service_name,
                "deployment.environment": settings.app.app_env,
            }
        )
        provider = TracerProvider(resource=resource)

        endpoint = settings.observability.otel_exporter_otlp_endpoint
        if endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                from shared.egress import check_url

                check_url(endpoint)
                provider.add_span_processor(
                    BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=endpoint.startswith("http://")))
                )
            except Exception as exc:  # noqa: BLE001 - tracing must never block startup
                logger.warning("otlp_export_disabled", endpoint=endpoint, error=str(exc))

        trace.set_tracer_provider(provider)

        if app is not None:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(app)
    except (ImportError, ModuleNotFoundError) as e:
        logger.warning("opentelemetry_sdk_missing", error=str(e), action="falling_back_to_noop")

    return trace.get_tracer(settings.observability.otel_service_name)


def get_tracer() -> trace.Tracer:
    """Get the global tracer instance."""
    settings = get_settings()
    return trace.get_tracer(settings.observability.otel_service_name)


# ─── Langfuse Observability ──────────────────────────────────────


def get_langfuse_handler(
    session_id: str | None = None,
    agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any | None:  # noqa: ANN401
    """
    Build a Langfuse CallbackHandler, or None when tracing is off, unconfigured, unavailable, or blocked.

    Fails closed: the Langfuse host must pass the egress policy (in-VPC by default), so traces cannot
    leave the sandbox through a hosted instance. The langfuse package is optional.
    """
    obs = get_settings().observability
    if not obs.langfuse_enabled or not (obs.langfuse_public_key and obs.langfuse_secret_key):
        return None

    logger = get_logger("langfuse_setup")
    try:
        from shared.egress import check_url

        host = check_url(obs.langfuse_host)
    except Exception as exc:  # noqa: BLE001 - tracing must never break an investigation
        logger.warning("langfuse_blocked_by_egress_policy", host=obs.langfuse_host, error=str(exc))
        return None

    try:
        try:
            from langfuse.callback import CallbackHandler  # langfuse 2.x
        except ImportError:
            CallbackHandler = None  # noqa: N806
            from langfuse import Langfuse
            from langfuse.langchain import CallbackHandler as V3Handler  # langfuse 3.x
    except ImportError:
        logger.warning("langfuse_package_missing", action="tracing_disabled")
        return None

    try:
        if CallbackHandler is None:
            # v3 takes no host/secret/session arguments: the client carries them (egress-checked host),
            # and the handler binds to it by public key.
            Langfuse(public_key=obs.langfuse_public_key, secret_key=obs.langfuse_secret_key, host=host)
            logger.info("langfuse_v3_handler", note="session set via langfuse_session_context")
            return V3Handler(public_key=obs.langfuse_public_key)
        return CallbackHandler(
            public_key=obs.langfuse_public_key,
            secret_key=obs.langfuse_secret_key,
            host=host,
            session_id=session_id,
            user_id="sre-system",
            tags=[f"agent:{agent}", f"env:{get_settings().app.app_env}"] if agent else None,
            metadata=metadata,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("langfuse_init_failed", error=str(exc))
        return None


def langfuse_session_context(session_id: str) -> AbstractContextManager[Any]:
    """
    Groups every Langfuse trace of one investigation under `session_id` on Langfuse 3.x, where the
    handler no longer takes a session id. A no-op when tracing is off or the package is the 2.x line
    (2.x sets the session on each handler instead).
    """
    obs = get_settings().observability
    if not obs.langfuse_enabled or not (obs.langfuse_public_key and obs.langfuse_secret_key):
        return nullcontext()
    try:
        from langfuse import propagate_attributes
    except ImportError:
        return nullcontext()
    return propagate_attributes(session_id=session_id, user_id="sre-system")


def get_langfuse_callbacks(session_id: str | None = None) -> list[Any]:
    """List form of `get_langfuse_handler` (empty when tracing is unavailable)."""
    handler = get_langfuse_handler(session_id=session_id)
    return [handler] if handler else []


# ─── Redis Client ────────────────────────────────────────────────


async def get_redis_client() -> aioredis.Redis:
    """Create an async Redis client."""
    settings = get_settings()
    return aioredis.from_url(
        settings.data.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )


async def acquire_distributed_lock(lock_key: str, ttl_seconds: int = 300) -> bool:
    """
    Attempt to acquire a distributed lock using Redis.
    Uses SETNX semantics.

    Returns:
        True if the lock was acquired, False if it was already held.
    """
    redis = await get_redis_client()
    try:
        acquired = await redis.set(lock_key, "locked", ex=ttl_seconds, nx=True)
        return bool(acquired)
    except Exception as e:
        logger = get_logger("distributed_lock")
        logger.error("lock_acquisition_error", key=lock_key, error=str(e))
        # Fail open or closed depending on requirements. Choosing fail-open for telemetry.
        return True
    finally:
        await redis.aclose()


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


class BudgetExceededError(Exception):
    """Raised when an agent exceeds its token or cost budget."""

    pass


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
        cost = (input_tokens / 1000 * cost_rates["input"]) + (output_tokens / 1000 * cost_rates["output"])
        self.total_cost += cost

        self.calls.append(
            {
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost, 6),
                "cumulative_tokens": self.total_tokens,
            }
        )

        if self.budget_exceeded:
            logger = get_logger("cost_tracker")
            logger.error("budget_exceeded", incident_id=self.incident_id, tokens=self.total_tokens, max=self.max_tokens)
            raise BudgetExceededError(f"Incident {self.incident_id} exceeded token budget of {self.max_tokens}")

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
