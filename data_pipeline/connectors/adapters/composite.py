"""
Composite Telemetry Adapter.

Fans a single query out to several vendor adapters concurrently and merges the
normalized records. A vendor failure is recorded (never replaced with mock data);
the call only fails when every vendor fails.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter, TelemetrySourceError
from shared.schemas import NormalizedTelemetryRecord, TelemetryConnectorSource
from shared.utils import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

logger = get_logger("composite_adapter")


class CompositeTelemetryAdapter(BaseTelemetryAdapter):
    def __init__(self, adapters: Sequence[BaseTelemetryAdapter]) -> None:
        if not adapters:
            raise ValueError("CompositeTelemetryAdapter requires at least one adapter")
        super().__init__(source=TelemetryConnectorSource.CUSTOM)
        self.adapters = list(adapters)
        self.last_failures: dict[str, str] = {}

    async def close(self) -> None:
        for adapter in self.adapters:
            closer = getattr(adapter, "close", None)
            if closer is not None:
                await closer()

    async def health_check(self) -> bool:
        results = await asyncio.gather(*(a.health_check() for a in self.adapters), return_exceptions=True)
        return any(r is True for r in results)

    async def _fan_out(self, call: Any) -> list[NormalizedTelemetryRecord]:
        results = await asyncio.gather(*(call(a) for a in self.adapters), return_exceptions=True)
        merged: list[NormalizedTelemetryRecord] = []
        failures: dict[str, str] = {}
        for adapter, result in zip(self.adapters, results, strict=True):
            if isinstance(result, TelemetrySourceError):
                failures[adapter.source.value] = str(result)
            elif isinstance(result, BaseException):
                raise result
            else:
                merged.extend(result)
        self.last_failures = failures
        if failures and len(failures) == len(self.adapters):
            raise TelemetrySourceError("; ".join(f"{k}: {v}" for k, v in failures.items()))
        if failures:
            logger.warning("composite_partial_failure", failed=sorted(failures))
        return merged

    async def fetch_signals(
        self,
        entity: str,
        metric_names: list[str],
        time_window_seconds: int = 300,
    ) -> list[NormalizedTelemetryRecord]:
        return await self._fan_out(lambda a: a.fetch_signals(entity, metric_names, time_window_seconds))

    async def query_signal(
        self,
        query: str,
        entity: str | None = None,
        tags: dict[str, Any] | None = None,
    ) -> list[NormalizedTelemetryRecord]:
        return await self._fan_out(lambda a: a.query_signal(query, entity, tags))

    async def stream_signals(
        self,
        poll_interval_seconds: float = 5.0,
        entities: list[str] | None = None,
    ) -> AsyncGenerator[NormalizedTelemetryRecord, None]:
        queue: asyncio.Queue[NormalizedTelemetryRecord] = asyncio.Queue()

        async def pump(adapter: BaseTelemetryAdapter) -> None:
            try:
                async for rec in adapter.stream_signals(poll_interval_seconds, entities):
                    await queue.put(rec)
            except TelemetrySourceError as exc:
                self.last_failures[adapter.source.value] = str(exc)

        tasks = [asyncio.create_task(pump(a)) for a in self.adapters]
        try:
            while True:
                yield await queue.get()
        finally:
            for t in tasks:
                t.cancel()
