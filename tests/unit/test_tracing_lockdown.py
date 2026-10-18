import os

from shared.config import get_settings
from shared.utils import get_langfuse_handler


def test_langsmith_forced_off_for_public_endpoint(monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "lsv2_test")
    get_settings.cache_clear()
    try:
        get_settings()
        assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
        assert "LANGCHAIN_API_KEY" not in os.environ
    finally:
        get_settings.cache_clear()


def test_langfuse_handler_none_without_keys(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "")
    get_settings.cache_clear()
    try:
        assert get_langfuse_handler("s", "agent") is None
    finally:
        get_settings.cache_clear()


def test_setup_tracing_without_endpoint_does_not_export(monkeypatch):
    from fastapi import FastAPI

    from shared.utils import setup_tracing

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    get_settings.cache_clear()
    try:
        assert setup_tracing(FastAPI()) is not None
    finally:
        get_settings.cache_clear()


def test_langfuse_handler_none_for_blocked_host(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    monkeypatch.setenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
    monkeypatch.setenv("EGRESS_ALLOWED_HOSTS", "*.modal.run")
    get_settings.cache_clear()
    try:
        assert get_langfuse_handler("s", "agent") is None
    finally:
        get_settings.cache_clear()
