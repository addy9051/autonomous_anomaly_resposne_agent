from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from agents.verification.verifier import RecoveryVerifier
from shared.schemas import (
    ActionResult,
    ActionTier,
    IncidentRecord,
    NormalizedTelemetryRecord,
    TelemetryConnectorSource,
    VerificationOutcome,
)


class FakeTelemetryAdapter:
    source = TelemetryConnectorSource.PROMETHEUS

    def __init__(self, records: list[NormalizedTelemetryRecord]) -> None:
        self.fetch_signals = AsyncMock(return_value=records)


def _record(
    metric_name: str,
    value: float,
    *,
    timestamp: datetime | None = None,
    connector_source: TelemetryConnectorSource = TelemetryConnectorSource.PROMETHEUS,
    tags: dict[str, bool] | None = None,
) -> NormalizedTelemetryRecord:
    return NormalizedTelemetryRecord(
        entity="payments",
        metric_name=metric_name,
        value=value,
        timestamp=timestamp or datetime.now(UTC),
        connector_source=connector_source,
        tags=tags or {},
    )


def _successful_action() -> ActionResult:
    return ActionResult(
        incident_id="incident-verifier-test",
        action_taken="scale_replicas",
        tier=ActionTier.TIER_1_AUTO,
        execution_status="success",
    )


@pytest.mark.asyncio
async def test_live_verification_rejects_simulator_evidence() -> None:
    adapter = FakeTelemetryAdapter([
        _record(
            "error_rate",
            0.0,
            connector_source=TelemetryConnectorSource.SIMULATOR,
            tags={"simulated": True},
        ),
        _record(
            "latency_p99",
            20.0,
            connector_source=TelemetryConnectorSource.SIMULATOR,
            tags={"is_simulated": True},
        ),
    ])
    verifier = RecoveryVerifier(stabilization_window_seconds=0)

    result = await verifier.verify(
        IncidentRecord(incident_id="incident-verifier-test"),
        [_successful_action()],
        telemetry_adapter=adapter,
        dry_run=False,
    )

    assert result.outcome == VerificationOutcome.INCONCLUSIVE


@pytest.mark.asyncio
async def test_verification_requires_all_required_signals() -> None:
    adapter = FakeTelemetryAdapter([_record("error_rate", 0.0)])
    verifier = RecoveryVerifier(stabilization_window_seconds=0)

    result = await verifier.verify(
        IncidentRecord(incident_id="incident-verifier-test"),
        [_successful_action()],
        telemetry_adapter=adapter,
        dry_run=False,
    )

    assert result.outcome == VerificationOutcome.INCONCLUSIVE
    assert result.checked_signals["missing_required"] == ["latency_p99"]


@pytest.mark.asyncio
async def test_verification_rejects_stale_evidence() -> None:
    stale_timestamp = datetime.now(UTC) - timedelta(seconds=300)
    adapter = FakeTelemetryAdapter([
        _record("error_rate", 0.0, timestamp=stale_timestamp, connector_source=TelemetryConnectorSource.PROMETHEUS),
        _record("latency_p99", 10.0, timestamp=stale_timestamp, connector_source=TelemetryConnectorSource.PROMETHEUS),
    ])
    verifier = RecoveryVerifier(stabilization_window_seconds=0)

    result = await verifier.verify(
        IncidentRecord(incident_id="incident-verifier-test"),
        [_successful_action()],
        telemetry_adapter=adapter,
        dry_run=False,
    )

    assert result.outcome == VerificationOutcome.INCONCLUSIVE


@pytest.mark.asyncio
async def test_verification_applies_policy_thresholds() -> None:
    adapter = FakeTelemetryAdapter([
        _record("error_rate", 0.5, connector_source=TelemetryConnectorSource.PROMETHEUS),
        _record("latency_p99", 200.0, connector_source=TelemetryConnectorSource.PROMETHEUS),
    ])
    verifier = RecoveryVerifier(stabilization_window_seconds=0)

    result = await verifier.verify(
        IncidentRecord(incident_id="incident-verifier-test"),
        [_successful_action()],
        telemetry_adapter=adapter,
        dry_run=False,
        verification_criteria={"error_rate_threshold": 0.1, "latency_threshold_ms": 250.0},
    )

    assert result.outcome == VerificationOutcome.NOT_RECOVERED
