"""
Story-Based Incident Simulation Engine.

Generates realistic multi-step incident narrative streams for enterprise demos
and autonomous agent validation, as specified in the SRE Advisory Blueprint.

Scenarios:
1. 'The Bad Deploy': Deployment change -> Latency spike -> Error surge -> Rollback correlation.
2. 'Database Resource Lock': Constant traffic -> DB CPU 99% + pool exhaustion -> Slow query contention.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from shared.schemas import (
    NormalizedTelemetryRecord,
    Severity,
    TelemetryConnectorSource,
    TelemetrySignalType,
)
from shared.utils import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = get_logger("story_simulation")


class IncidentScenario(StrEnum):
    """Supported story-based incident narrative scenarios."""

    BAD_DEPLOY = "bad_deploy"
    DATABASE_LOCK = "database_lock"


class StorySimulationEngine:
    """
    Produces deterministic, chronologically ordered incident stories.
    """

    def __init__(self) -> None:
        logger.info("story_simulation_engine_initialized")

    def generate_bad_deploy_scenario(
        self,
        base_time: datetime | None = None,
        service: str = "checkout-api",
    ) -> list[NormalizedTelemetryRecord]:
        """
        Scenario 1: 'The Bad Deploy' Outage.

        Timeline:
        T=0:      Deployment event occurs (v1.0.0 -> v1.1.0).
        T+1 min:  p99 latency spikes from 180ms to 2400ms.
        T+2 mins: HTTP 504 Gateway Timeout error rate spikes to 6.2%.
        """
        start = base_time or datetime.now(UTC)
        records: list[NormalizedTelemetryRecord] = []

        # Step 1: T=0 - Deployment Event
        records.append(
            NormalizedTelemetryRecord(
                timestamp=start,
                entity=service,
                connector_source=TelemetryConnectorSource.SIMULATOR,
                signal_type=TelemetrySignalType.BUSINESS_SIGNAL,
                metric_name="deployment_event",
                value=1.0,
                unit="event",
                baseline_mean=0.0,
                deviation_sigma=0.0,
                severity=Severity.LOW,
                tags={
                    "event_type": "canary_rollout",
                    "previous_version": "v1.0.0",
                    "deployment_version": "v1.1.0",
                    "commit_sha": "7f8b9a2",
                    "author": "release-bot@fintech.internal",
                },
                raw_query="deployments.history{service:checkout-api}",
            )
        )

        # Step 2: T+1 min - Latency Spike
        records.append(
            NormalizedTelemetryRecord(
                timestamp=start + timedelta(minutes=1),
                entity=service,
                connector_source=TelemetryConnectorSource.SIMULATOR,
                signal_type=TelemetrySignalType.METRIC,
                metric_name="latency_p99_ms",
                value=2400.0,
                unit="ms",
                baseline_mean=180.0,
                deviation_sigma=4.8,
                severity=Severity.HIGH,
                tags={
                    "deployment_version": "v1.1.0",
                    "region": "us-east-1",
                    "cluster": "prod-k8s-us",
                },
                raw_query="avg:checkout.latency.p99{env:prod}",
            )
        )

        # Step 3: T+2 mins - Error Rate Surge
        records.append(
            NormalizedTelemetryRecord(
                timestamp=start + timedelta(minutes=2),
                entity=service,
                connector_source=TelemetryConnectorSource.SIMULATOR,
                signal_type=TelemetrySignalType.METRIC,
                metric_name="error_rate",
                value=6.2,
                unit="percent",
                baseline_mean=0.10,
                deviation_sigma=5.5,
                severity=Severity.CRITICAL,
                tags={
                    "deployment_version": "v1.1.0",
                    "http_status": "504",
                    "error_code": "GATEWAY_TIMEOUT",
                },
                raw_query="avg:checkout.http.errors.rate{env:prod}",
            )
        )

        return records

    def generate_database_lock_scenario(
        self,
        base_time: datetime | None = None,
        service: str = "payment-service-prod",
        db_entity: str = "payment-postgres-db",
    ) -> list[NormalizedTelemetryRecord]:
        """
        Scenario 2: Database Connection & Resource Lock.

        Timeline:
        T=0:      Baseline user traffic remains steady (500 req/sec) with normal app CPU (18%).
        T+1 min:  Database CPU spikes to 99% and connection pool reaches max capacity (100/100).
        T+2 mins: Transaction latency climbs to 3200ms while application server CPU stays low (<20%).
        """
        start = base_time or datetime.now(UTC)
        records: list[NormalizedTelemetryRecord] = []

        # Step 1: T=0 - Normal steady traffic baseline
        records.append(
            NormalizedTelemetryRecord(
                timestamp=start,
                entity=service,
                connector_source=TelemetryConnectorSource.SIMULATOR,
                signal_type=TelemetrySignalType.METRIC,
                metric_name="request_rate",
                value=500.0,
                unit="req/sec",
                baseline_mean=500.0,
                deviation_sigma=0.05,
                severity=Severity.LOW,
                tags={
                    "app_cpu_percent": 18.2,
                    "status": "normal_operation",
                },
                raw_query="sum:payment.requests.rate{env:prod}",
            )
        )

        # Step 2: T+1 min - Database Resource Contention & Pool Lock
        records.append(
            NormalizedTelemetryRecord(
                timestamp=start + timedelta(minutes=1),
                entity=db_entity,
                connector_source=TelemetryConnectorSource.SIMULATOR,
                signal_type=TelemetrySignalType.METRIC,
                metric_name="database_cpu_percent",
                value=99.2,
                unit="percent",
                baseline_mean=35.0,
                deviation_sigma=6.1,
                severity=Severity.CRITICAL,
                tags={
                    "active_connections": 100,
                    "max_connections": 100,
                    "pool_exhausted": True,
                    "blocked_queries_count": 48,
                    "top_wait_event": "Lock:relation",
                },
                raw_query="avg:postgresql.cpu.percent{host:payment-db}",
            )
        )

        # Step 3: T+2 mins - Transaction Delay with low app CPU (Resource Contention Signature)
        records.append(
            NormalizedTelemetryRecord(
                timestamp=start + timedelta(minutes=2),
                entity=service,
                connector_source=TelemetryConnectorSource.SIMULATOR,
                signal_type=TelemetrySignalType.METRIC,
                metric_name="transaction_latency_ms",
                value=3200.0,
                unit="ms",
                baseline_mean=150.0,
                deviation_sigma=5.8,
                severity=Severity.CRITICAL,
                tags={
                    "app_server_cpu_percent": 19.4,
                    "database_bound": True,
                    "diagnosis_hint": "unindexed_query_lock",
                },
                raw_query="avg:payment.transaction.latency.ms{env:prod}",
            )
        )

        return records

    def get_scenario_events(
        self,
        scenario: IncidentScenario | str,
        base_time: datetime | None = None,
    ) -> list[NormalizedTelemetryRecord]:
        """Retrieve telemetry records for a named scenario."""
        scenario_key = str(scenario).lower()
        if scenario_key in [IncidentScenario.BAD_DEPLOY.value, "bad_deploy", "deploy"]:
            return self.generate_bad_deploy_scenario(base_time=base_time)
        elif scenario_key in [IncidentScenario.DATABASE_LOCK.value, "database_lock", "db_lock", "lock"]:
            return self.generate_database_lock_scenario(base_time=base_time)
        else:
            raise ValueError(f"Unknown scenario '{scenario}'. Supported: {[s.value for s in IncidentScenario]}")

    async def stream_scenario(
        self,
        scenario: IncidentScenario | str,
        delay_between_steps: float = 1.5,
    ) -> AsyncGenerator[NormalizedTelemetryRecord, None]:
        """Asynchronously stream scenario events with real-time delays."""
        events = self.get_scenario_events(scenario)
        for record in events:
            yield record
            await asyncio.sleep(delay_between_steps)

    @staticmethod
    def list_scenarios() -> list[dict[str, Any]]:
        """Return metadata for all available simulation scenarios."""
        return [
            {
                "id": IncidentScenario.BAD_DEPLOY.value,
                "name": "The Bad Deploy Outage",
                "description": (
                    "Service deployment updates from v1.0.0 to v1.1.0, triggering "
                    "a 2400ms latency spike and a 6.2% 504 error surge. Recommends automated rollback."
                ),
                "recommended_action": "rollback_deployment",
            },
            {
                "id": IncidentScenario.DATABASE_LOCK.value,
                "name": "Database Connection & Resource Lock",
                "description": (
                    "Normal app traffic with DB CPU peaking at 99% and connection pool exhaustion (100/100). "
                    "App server CPU remains low (<20%), isolating a database query lock."
                ),
                "recommended_action": "kill_blocking_queries",
            },
        ]
