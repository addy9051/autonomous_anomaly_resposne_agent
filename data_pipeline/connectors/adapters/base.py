"""
Base Telemetry Adapter Interface.

Defines the contract for all Observability Platform API connectors
(Datadog, Splunk, Prometheus, and Simulator). All connectors normalize
vendor-specific outputs into NormalizedTelemetryRecord instances.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from shared.schemas import NormalizedTelemetryRecord, TelemetryConnectorSource

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


class BaseTelemetryAdapter(ABC):
    """
    Abstract Base Class for Observability Platform API Connectors.

    Provides a uniform interface to query, poll, or stream metrics and log
    summaries, decoupling agents from underlying observability vendor APIs.
    """

    def __init__(self, source: TelemetryConnectorSource) -> None:
        self.source = source

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify connectivity and authentication with the target observability platform API."""
        ...

    @abstractmethod
    async def fetch_signals(
        self,
        entity: str,
        metric_names: list[str],
        time_window_seconds: int = 300,
    ) -> list[NormalizedTelemetryRecord]:
        """
        Fetch point-in-time or windowed signals for a specific service or entity.

        Args:
            entity: Target service or host identifier (e.g. "payment-service-prod").
            metric_names: List of metric or signal names to query.
            time_window_seconds: Lookback duration in seconds.

        Returns:
            List of standardized NormalizedTelemetryRecord objects.
        """
        ...

    @abstractmethod
    async def query_signal(
        self,
        query: str,
        entity: str | None = None,
        tags: dict[str, Any] | None = None,
    ) -> list[NormalizedTelemetryRecord]:
        """
        Execute an arbitrary vendor-specific query (e.g. PromQL, Datadog metric expression, Splunk search).

        Args:
            query: Platform query string.
            entity: Optional target entity name.
            tags: Optional key-value filter tags.

        Returns:
            List of standardized NormalizedTelemetryRecord objects.
        """
        ...

    @abstractmethod
    def stream_signals(
        self,
        poll_interval_seconds: float = 5.0,
        entities: list[str] | None = None,
    ) -> AsyncGenerator[NormalizedTelemetryRecord, None]:
        """
        Stream or poll continuous telemetry signals from the platform API.

        Args:
            poll_interval_seconds: Interval between API polls.
            entities: Optional list of services to filter on.

        Yields:
            Standardized NormalizedTelemetryRecord events.
        """
        ...
