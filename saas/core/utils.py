"""
Logging and Utility Functions for SaaS Platform.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

import structlog


def setup_logging(log_level: str = "INFO") -> None:
    """Configure structured JSON logging for Cloud Run / Render / local console."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=getattr(logging, log_level.upper(), logging.INFO))

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer() if log_level.upper() == "INFO" else structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structured logger."""
    return structlog.get_logger(name)


def utc_now() -> datetime:
    """Return current timezone-aware UTC datetime."""
    return datetime.now(UTC)


def json_dumps_canonical(obj: Any) -> str:
    """Deterministic JSON string for hashing and comparison."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
