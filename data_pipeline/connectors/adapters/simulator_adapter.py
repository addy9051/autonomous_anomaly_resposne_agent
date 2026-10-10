"""
Simulator Telemetry Adapter.

Produces realistic simulated telemetry records matching the NormalizedTelemetryRecord contract.
Used for local development, demos, and testing without requiring live external platform credentials.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import numpy as np

from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter
from shared.schemas import (
    NormalizedTelemetryRecord,
    Severity,
    TelemetryConnectorSource,
    TelemetrySignalType,
)
from shared.utils import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = get_logger("simulator_adapter")


class SimulatorAdapter(BaseTelemetryAdapter):
    """
    Adapter that simulates Observability Platform API responses.

    Generates normalized metrics, baseline averages, and statistical deviations
    mimicking responses from Datadog, Splunk, and Prometheus.
    """

    def __init__(self, seed: int = 42) -> None:
        super().__init__(source=TelemetryConnectorSource.SIMULATOR)
        self.rng = np.random.default_rng(seed)
        self.default_entities = [
            "payment-service-prod",
            "checkout-api",
            "auth-service",
            "fraud-detection-model",
            "settlement-engine",
        ]
        logger.info("simulator_adapter_initialized")

    async def health_check(self) -> bool:
        """Simulator is always available in local and demo environments."""
        return True

    async def fetch_signals(
        self,
        entity: str,
        metric_names: list[str],
        time_window_seconds: int = 300,
    ) -> list[NormalizedTelemetryRecord]:
        """Fetch simulated metrics for an entity with calculated baselines."""
        records: list[NormalizedTelemetryRecord] = []
        now = datetime.now(timezone.utc)

        for metric in metric_names:
            baseline = 100.0 if "latency" in metric else 1.0
            value = float(self.rng.normal(baseline, baseline * 0.1))
            sigma = float(abs(value - baseline) / (baseline * 0.1 + 1e-6))

            records.append(
                NormalizedTelemetryRecord(
                    timestamp=now,
                    entity=entity,
                    connector_source=self.source,
                    signal_type=TelemetrySignalType.METRIC,
                    metric_name=metric,
                    value=round(value, 2),
                    unit="ms" if "latency" in metric else "ratio",
                    baseline_mean=round(baseline, 2),
                    deviation_sigma=round(sigma, 2),
                    severity=Severity.HIGH if sigma > 3.0 else Severity.LOW,
                    tags={"simulated": True, "window_seconds": time_window_seconds},
                )
            )

        return records

    async def query_signal(
        self,
        query: str,
        entity: str | None = None,
        tags: dict[str, Any] | None = None,
    ) -> list[NormalizedTelemetryRecord]:
        """Execute a simulated query returning matching normalized records."""
        target_entity = entity or self.rng.choice(self.default_entities)
        metric_name = query.split("{")[0] if "{" in query else query
        value = float(self.rng.normal(50.0, 5.0))

        return [
            NormalizedTelemetryRecord(
                timestamp=datetime.now(timezone.utc),
                entity=target_entity,
                connector_source=self.source,
                signal_type=TelemetrySignalType.METRIC,
                metric_name=metric_name,
                value=round(value, 2),
                unit="value",
                baseline_mean=50.0,
                deviation_sigma=0.5,
                severity=Severity.LOW,
                tags=tags or {"query": query},
                raw_query=query,
            )
        ]

    async def stream_signals(
        self,
        poll_interval_seconds: float = 5.0,
        entities: list[str] | None = None,
    ) -> AsyncGenerator[NormalizedTelemetryRecord, None]:
        """Continuously yield simulated normalized telemetry records."""
        target_entities = entities or self.default_entities

        while True:
            entity = self.rng.choice(target_entities)
            is_anomaly = self.rng.random() < 0.10

            if is_anomaly:
                metric_name = "transaction_failure_rate"
                value = round(float(self.rng.uniform(3.0, 8.5)), 2)
                baseline = 0.15
                sigma = 4.2
                severity = Severity.CRITICAL
                signal_type = TelemetrySignalType.METRIC
                unit = "percent"
            else:
                metric_name = "latency_p99"
                value = round(float(self.rng.normal(120.0, 15.0)), 2)
                baseline = 115.0
                sigma = 0.4
                severity = Severity.LOW
                signal_type = TelemetrySignalType.METRIC
                unit = "ms"

            yield NormalizedTelemetryRecord(
                timestamp=datetime.now(timezone.utc),
                entity=entity,
                connector_source=self.source,
                signal_type=signal_type,
                metric_name=metric_name,
                value=value,
                unit=unit,
                baseline_mean=baseline,
                deviation_sigma=sigma,
                severity=severity,
                tags={"environment": "production", "region": "us-east-1"},
            )

            await asyncio.sleep(poll_interval_seconds)
