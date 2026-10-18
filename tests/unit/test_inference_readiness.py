from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

import api

if TYPE_CHECKING:
    from types import TracebackType

    from _pytest.monkeypatch import MonkeyPatch


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _Client:
    def __init__(self, status_code: int | None = None, error: Exception | None = None) -> None:
        self.status_code = status_code
        self.error = error

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None

    async def get(self, url: str, headers: dict[str, str]) -> _Response:
        assert url == "http://127.0.0.1:8000/v1/models"
        assert headers == {"Authorization": "Bearer test-key"}
        if self.error:
            raise self.error
        return _Response(self.status_code or 200)


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(200, "ready"), (303, "unavailable"), (401, "unavailable"), (503, "starting")],
)
@pytest.mark.asyncio
async def test_inference_endpoint_readiness(
    monkeypatch: MonkeyPatch, status_code: int, expected: str
) -> None:
    monkeypatch.setattr(api, "guarded_async_client", lambda **_: _Client(status_code=status_code))

    result = await api._probe_inference_endpoint("http://127.0.0.1:8000/v1", "test-key")

    assert result == expected


@pytest.mark.asyncio
async def test_inference_timeout_means_starting(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        api,
        "guarded_async_client",
        lambda **_: _Client(error=httpx.TimeoutException("model is loading")),
    )

    result = await api._probe_inference_endpoint("http://127.0.0.1:8000/v1", "test-key")

    assert result == "starting"


@pytest.mark.asyncio
async def test_inference_endpoint_without_url_is_unavailable() -> None:
    assert await api._probe_inference_endpoint("", "test-key") == "unavailable"
