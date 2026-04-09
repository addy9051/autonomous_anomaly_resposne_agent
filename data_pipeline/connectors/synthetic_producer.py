"""
Synthetic Telemetry Producer.

Generates realistic payment telemetry events for development and testing.
Supports both Kafka and in-memory event bus output.

Event types generated:
- Payment gateway transactions (normal + anomalous)
- Infrastructure metrics (CPU, memory, latency)
- Fraud detection model outputs
- CS queue events
"""

from __future__ import annotations

import asyncio
import uuid
from typing import TYPE_CHECKING

import numpy as np

from shared.schemas import TelemetryEvent
from shared.utils import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = get_logger("synthetic_producer")


class SyntheticTelemetryProducer:
    """
    Generates realistic synthetic telemetry events for development.

    Can inject anomalies on demand to test the agent pipeline.
    """

    def __init__(self, seed: int = 42) -> None:
        self.rng = np.random.default_rng(seed)
        self.service_names = [
            "payment-gateway",
            "fraud-api",
            "auth-service",
            "merchant-api",
            "settlement-engine",
            "notification-service",
        ]
        self.event_count = 0
        logger.info("synthetic_producer_initialized")

    def generate_normal_transaction(self) -> TelemetryEvent:
        """Generate a normal payment transaction event."""
        self.event_count += 1
        service = self.rng.choice(self.service_names[:3])  # Payment services

        return TelemetryEvent(
            source="payment_gateway",
            service_name=service,
            event_type="transaction",
            payload={
                "txn_id": str(uuid.uuid4()),
                "amount": round(float(self.rng.lognormal(4, 1.5)), 2),
                "currency": self.rng.choice(["USD", "EUR", "GBP", "JPY"]),
                "status": self.rng.choice(["approved", "approved", "approved", "declined"]),
                "latency_ms": round(float(self.rng.lognormal(5, 0.3)), 2),  # ~150ms normal
                "merchant_id": f"merch_{self.rng.integers(1000, 9999)}",
                "risk_score": round(float(self.rng.beta(2, 50)), 4),  # Mostly low
                "card_type": self.rng.choice(["visa", "mastercard", "amex"]),
                "region": self.rng.choice(["us-east", "us-west", "eu-west", "ap-southeast"]),
            },
        )

    def generate_normal_metrics(self) -> TelemetryEvent:
        """Generate normal infrastructure metrics."""
        self.event_count += 1
        service = self.rng.choice(self.service_names)

        return TelemetryEvent(
            source="infra_metrics",
            service_name=service,
            event_type="metric",
            payload={
                "pod_name": f"{service}-{self.rng.integers(1, 5)}-{uuid.uuid4().hex[:8]}",
                "cpu_percent": round(float(self.rng.normal(45, 12)), 2),
                "memory_percent": round(float(self.rng.normal(60, 8)), 2),
                "latency_p50_ms": round(float(self.rng.lognormal(4.5, 0.2)), 2),
                "latency_p95_ms": round(float(self.rng.lognormal(5.0, 0.3)), 2),
                "latency_p99_ms": round(float(self.rng.lognormal(5.2, 0.4)), 2),
                "error_rate": round(float(self.rng.beta(1, 50)), 4),
                "request_rate": round(float(self.rng.poisson(5000)), 2),
                "active_connections": int(self.rng.poisson(200)),
                "kafka_consumer_lag": int(self.rng.exponential(300)),
            },
        )

    def generate_anomalous_event(self, anomaly_type: str = "latency_spike") -> TelemetryEvent:
        """
        Generate an anomalous telemetry event for testing.

        Supported anomaly types:
        - latency_spike: p99 latency 5-10x normal
        - error_rate: Error rate > 10%
        - fraud_signal: Elevated fraud scores
        - resource_saturation: CPU/memory near 100%
        - volume_anomaly: Request volume 3x normal
        """
        self.event_count += 1
        service = self.rng.choice(self.service_names[:2])

        if anomaly_type == "latency_spike":
            return TelemetryEvent(
                source="infra_metrics",
                service_name=service,
                event_type="metric",
                payload={
                    "pod_name": f"{service}-1-{uuid.uuid4().hex[:8]}",
                    "cpu_percent": round(float(self.rng.normal(75, 10)), 2),
                    "memory_percent": round(float(self.rng.normal(80, 5)), 2),
                    "latency_p50_ms": round(float(self.rng.normal(500, 100)), 2),
                    "latency_p95_ms": round(float(self.rng.normal(1200, 200)), 2),
                    "latency_p99_ms": round(float(self.rng.normal(2500, 500)), 2),  # 10x normal
                    "error_rate": round(float(self.rng.normal(0.08, 0.02)), 4),
                    "request_rate": round(float(self.rng.poisson(6000)), 2),
                    "active_connections": int(self.rng.poisson(450)),  # Near pool limit
                    "kafka_consumer_lag": int(self.rng.exponential(5000)),
                    "_anomaly_injected": True,
                    "_anomaly_type": "latency_spike",
                },
            )

        elif anomaly_type == "error_rate":
            return TelemetryEvent(
                source="infra_metrics",
                service_name=service,
                event_type="metric",
                payload={
                    "pod_name": f"{service}-2-{uuid.uuid4().hex[:8]}",
                    "cpu_percent": round(float(self.rng.normal(60, 10)), 2),
                    "memory_percent": round(float(self.rng.normal(70, 5)), 2),
                    "latency_p99_ms": round(float(self.rng.normal(400, 80)), 2),
                    "error_rate": round(float(self.rng.normal(0.15, 0.03)), 4),  # 15% error rate
                    "request_rate": round(float(self.rng.poisson(5000)), 2),
                    "5xx_count": int(self.rng.poisson(750)),
                    "4xx_count": int(self.rng.poisson(200)),
                    "_anomaly_injected": True,
                    "_anomaly_type": "error_rate",
                },
            )

        elif anomaly_type == "fraud_signal":
            return TelemetryEvent(
                source="fraud_api",
                service_name="fraud-api",
                event_type="metric",
                payload={
                    "fraud_score_mean": round(float(self.rng.normal(0.12, 0.03)), 4),  # 3x normal
                    "fraud_score_p95": round(float(self.rng.normal(0.45, 0.1)), 4),
                    "flagged_transactions": int(self.rng.poisson(150)),  # 5x normal
                    "model_version": "fraud_model_v3.2.1",
                    "feature_drift_score": round(float(self.rng.normal(0.25, 0.05)), 4),
                    "_anomaly_injected": True,
                    "_anomaly_type": "fraud_signal",
                },
            )

        elif anomaly_type == "resource_saturation":
            return TelemetryEvent(
                source="infra_metrics",
                service_name=service,
                event_type="metric",
                payload={
                    "pod_name": f"{service}-3-{uuid.uuid4().hex[:8]}",
                    "cpu_percent": round(float(self.rng.normal(95, 3)), 2),  # Near 100%
                    "memory_percent": round(float(self.rng.normal(92, 2)), 2),
                    "latency_p99_ms": round(float(self.rng.normal(800, 200)), 2),
                    "error_rate": round(float(self.rng.normal(0.05, 0.02)), 4),
                    "oom_kills": int(self.rng.poisson(3)),
                    "_anomaly_injected": True,
                    "_anomaly_type": "resource_saturation",
                },
            )

        else:  # volume_anomaly
            return TelemetryEvent(
                source="payment_gateway",
                service_name=service,
                event_type="metric",
                payload={
                    "request_rate": round(float(self.rng.poisson(15000)), 2),  # 3x normal
                    "latency_p99_ms": round(float(self.rng.normal(350, 60)), 2),
                    "error_rate": round(float(self.rng.normal(0.03, 0.01)), 4),
                    "queue_depth": int(self.rng.poisson(5000)),
                    "_anomaly_injected": True,
                    "_anomaly_type": "volume_anomaly",
                },
            )

    async def stream_events(
        self,
        events_per_second: float = 10.0,
        anomaly_probability: float = 0.05,
        duration_seconds: float | None = None,
    ) -> AsyncGenerator[TelemetryEvent, None]:
        """
        Generate a continuous stream of telemetry events.

        Args:
            events_per_second: Rate of event generation
            anomaly_probability: Probability each event is anomalous
            duration_seconds: Total stream duration (None = infinite)
        """
        interval = 1.0 / events_per_second
        start_time = asyncio.get_event_loop().time()

        while True:
            if duration_seconds and (asyncio.get_event_loop().time() - start_time) > duration_seconds:
                break

            if self.rng.random() < anomaly_probability:
                anomaly_type = self.rng.choice([
                    "latency_spike", "error_rate", "fraud_signal",
                    "resource_saturation", "volume_anomaly",
                ])
                yield self.generate_anomalous_event(anomaly_type)
            else:
                if self.rng.random() < 0.6:
                    yield self.generate_normal_transaction()
                else:
                    yield self.generate_normal_metrics()

            await asyncio.sleep(interval)

    def generate_batch(
        self, count: int = 100, anomaly_fraction: float = 0.05
    ) -> list[TelemetryEvent]:
        """Generate a batch of events with specified anomaly fraction."""
        events = []
        n_anomalies = int(count * anomaly_fraction)

        for _ in range(count - n_anomalies):
            if self.rng.random() < 0.6:
                events.append(self.generate_normal_transaction())
            else:
                events.append(self.generate_normal_metrics())

        anomaly_types = ["latency_spike", "error_rate", "fraud_signal", "resource_saturation"]
        for _ in range(n_anomalies):
            events.append(self.generate_anomalous_event(self.rng.choice(anomaly_types)))

        self.rng.shuffle(events)
        return events
