"""
Unit tests for Pydantic schemas.

Tests all inter-agent API contracts for:
- Valid data creates models correctly
- Invalid data raises validation errors
- Enums validate correctly
- Default values work as expected
- Serialization / deserialization roundtrips
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shared.schemas import (
    ActionTier,
    AnomalyEvent,
    AnomalyType,
    DiagnosisResult,
    IncidentRecord,
    IncidentStatus,
    MetricsSnapshot,
    RecommendedAction,
    RootCauseCategory,
    Severity,
    TelemetryConnectorSource,
    TelemetryEvent,
    TelemetryRecord,
    TelemetrySignalType,
    NormalizedTelemetryRecord,
)


class TestAnomalyEvent:
    """Tests for AnomalyEvent schema."""

    def test_create_valid_event(self) -> None:
        event = AnomalyEvent(
            severity=Severity.HIGH,
            affected_services=["payment-gateway"],
            anomaly_type=AnomalyType.LATENCY_SPIKE,
            metrics_snapshot=MetricsSnapshot(p99_latency_ms=1240.0, error_rate=0.08),
            reasoning="p99 latency exceeds 2x baseline",
            confidence=0.92,
        )
        assert event.severity == Severity.HIGH
        assert event.confidence == 0.92
        assert event.event_id  # Auto-generated
        assert event.timestamp  # Auto-generated
        assert len(event.affected_services) == 1

    def test_confidence_bounds(self) -> None:
        # Valid boundary values
        AnomalyEvent(
            severity=Severity.LOW,
            affected_services=["test"],
            anomaly_type=AnomalyType.ERROR_RATE,
            metrics_snapshot=MetricsSnapshot(),
            reasoning="test",
            confidence=0.0,
        )
        AnomalyEvent(
            severity=Severity.LOW,
            affected_services=["test"],
            anomaly_type=AnomalyType.ERROR_RATE,
            metrics_snapshot=MetricsSnapshot(),
            reasoning="test",
            confidence=1.0,
        )

        # Invalid: out of bounds
        with pytest.raises(ValueError):
            AnomalyEvent(
                severity=Severity.LOW,
                affected_services=["test"],
                anomaly_type=AnomalyType.ERROR_RATE,
                metrics_snapshot=MetricsSnapshot(),
                reasoning="test",
                confidence=1.5,
            )

    def test_serialization_roundtrip(self) -> None:
        event = AnomalyEvent(
            severity=Severity.CRITICAL,
            affected_services=["payment-gateway", "fraud-api"],
            anomaly_type=AnomalyType.FRAUD_SIGNAL,
            metrics_snapshot=MetricsSnapshot(fraud_score_mean=0.15),
            reasoning="Fraud score drift detected",
            confidence=0.88,
        )
        json_str = event.model_dump_json()
        restored = AnomalyEvent.model_validate_json(json_str)
        assert restored.severity == event.severity
        assert restored.confidence == event.confidence
        assert restored.event_id == event.event_id


class TestDiagnosisResult:
    """Tests for DiagnosisResult schema."""

    def test_create_valid_diagnosis(self) -> None:
        diagnosis = DiagnosisResult(
            event_id="test-event-123",
            root_cause="Database connection pool exhaustion due to slow queries",
            root_cause_category=RootCauseCategory.DATABASE,
            recommended_actions=[
                RecommendedAction(
                    action="scale_replicas",
                    tier=ActionTier.TIER_1_AUTO,
                    params={"replicas": 5},
                ),
                RecommendedAction(
                    action="kill_long_running_queries",
                    tier=ActionTier.TIER_2_APPROVE,
                    params={"threshold_seconds": 30},
                ),
            ],
            reasoning_chain="1. p99 latency spiked... 2. Connection pool at 95%...",
            confidence=0.87,
        )
        assert diagnosis.root_cause_category == RootCauseCategory.DATABASE
        assert len(diagnosis.recommended_actions) == 2
        assert not diagnosis.is_novel_incident

    def test_novel_incident_flag(self) -> None:
        diagnosis = DiagnosisResult(
            event_id="test-event-456",
            root_cause="Unknown pattern",
            root_cause_category=RootCauseCategory.UNKNOWN,
            recommended_actions=[],
            reasoning_chain="No matching runbooks found",
            confidence=0.4,
            is_novel_incident=True,
        )
        assert diagnosis.is_novel_incident


class TestIncidentRecord:
    """Tests for IncidentRecord lifecycle."""

    def test_create_default_incident(self) -> None:
        incident = IncidentRecord()
        assert incident.status == IncidentStatus.DETECTED
        assert incident.auto_resolved is False
        assert incident.false_positive is False
        assert incident.total_llm_tokens_used == 0

    def test_full_lifecycle(self) -> None:
        incident = IncidentRecord()

        # Detect
        assert incident.status == IncidentStatus.DETECTED

        # Diagnose
        incident.status = IncidentStatus.DIAGNOSING
        assert incident.status == IncidentStatus.DIAGNOSING

        # Resolve
        incident.status = IncidentStatus.RESOLVED
        incident.auto_resolved = True
        incident.resolved_at = datetime.now(timezone.utc)
        incident.time_to_mitigate_seconds = 120.0
        assert incident.auto_resolved
        assert incident.time_to_mitigate_seconds == 120.0



class TestActionTier:
    """Tests for ActionTier enum."""

    def test_tier_values(self) -> None:
        assert ActionTier.TIER_1_AUTO.value == 1
        assert ActionTier.TIER_2_APPROVE.value == 2
        assert ActionTier.TIER_3_HUMAN.value == 3


class TestNormalizedTelemetryRecord:
    """Tests for the new NormalizedTelemetryRecord schema from Observability APIs."""

    def test_create_normalized_record(self) -> None:
        record = NormalizedTelemetryRecord(
            entity="payment-service-prod",
            environment="production",
            connector_source=TelemetryConnectorSource.DATADOG,
            signal_type=TelemetrySignalType.METRIC,
            metric_name="transaction_failure_rate",
            value=2.85,
            unit="percent",
            baseline_mean=0.15,
            deviation_sigma=3.2,
            severity=Severity.CRITICAL,
            tags={"cluster": "us-east-1", "deployment_version": "v1.2.0"},
            raw_query="avg:payment.tx.failures{env:prod}",
        )
        assert record.entity == "payment-service-prod"
        assert record.connector_source == TelemetryConnectorSource.DATADOG
        assert record.signal_type == TelemetrySignalType.METRIC
        assert record.metric_name == "transaction_failure_rate"
        assert record.value == 2.85
        assert record.deviation_sigma == 3.2
        assert record.severity == Severity.CRITICAL
        assert record.tags["deployment_version"] == "v1.2.0"
        assert record.raw_query == "avg:payment.tx.failures{env:prod}"

    def test_normalized_record_defaults(self) -> None:
        record = NormalizedTelemetryRecord(
            entity="checkout-api",
            metric_name="latency_p99_ms",
            value=450.0,
        )
        assert record.connector_source == TelemetryConnectorSource.SIMULATOR
        assert record.signal_type == TelemetrySignalType.METRIC
        assert record.environment == "production"
        assert record.severity == Severity.MEDIUM
        assert record.tags == {}

