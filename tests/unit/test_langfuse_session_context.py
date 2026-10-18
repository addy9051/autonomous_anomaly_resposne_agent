"""Session grouping helper: inert unless Langfuse is configured, and never raises."""

from __future__ import annotations

from contextlib import nullcontext

import pytest

from shared import utils
from shared.config import get_settings


def test_noop_when_tracing_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        assert isinstance(utils.langfuse_session_context("evt-1"), type(nullcontext()))
        with utils.langfuse_session_context("evt-1"):
            pass
    finally:
        get_settings.cache_clear()


def test_enabled_context_is_usable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    get_settings.cache_clear()
    try:
        with utils.langfuse_session_context("evt-2"):
            pass
    finally:
        get_settings.cache_clear()
