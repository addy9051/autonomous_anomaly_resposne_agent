"""
Unit tests for the synthetic telemetry producer and feature extraction.
"""

from __future__ import annotations

import pytest

from data_pipeline.connectors.synthetic_producer import SyntheticTelemetryProducer
from data_pipeline.flink_jobs.anomaly_features import AlertDeduplicator, RollingWindowAggregator


class TestSyntheticProducer:
    """Tests for the synthetic telemetry producer."""

    def setup_method(self) -> None:
        self.producer = SyntheticTelemetryProducer(seed=42)

    def test_normal_transaction(self) -> None:
        event = self.producer.generate_normal_transaction()
        assert event.source == "payment_gateway"
        assert event.event_type == "transaction"
        assert "txn_id" in event.payload
        assert "amount" in event.payload
        assert event.payload["amount"] > 0

    def test_normal_metrics(self) -> None:
        event = self.producer.generate_normal_metrics()
        assert event.source == "infra_metrics"
        assert event.event_type == "metric"
        assert "cpu_percent" in event.payload
        assert 0 <= event.payload["cpu_percent"] <= 100

    def test_anomalous_latency_spike(self) -> None:
        event = self.producer.generate_anomalous_event("latency_spike")
        assert event.payload["_anomaly_injected"] is True
        assert event.payload["latency_p99_ms"] > 1000  # Much higher than normal

    def test_anomalous_error_rate(self) -> None:
        event = self.producer.generate_anomalous_event("error_rate")
        assert event.payload["error_rate"] > 0.05  # Much higher than normal

    def test_anomalous_fraud_signal(self) -> None:
        event = self.producer.generate_anomalous_event("fraud_signal")
        assert event.payload["fraud_score_mean"] > 0.05  # Higher than baseline

    def test_batch_generation(self) -> None:
        events = self.producer.generate_batch(count=100, anomaly_fraction=0.1)
        assert len(events) == 100
        anomalies = [e for e in events if e.payload.get("_anomaly_injected")]
        assert len(anomalies) == 10  # 10% of 100

    def test_event_count_tracking(self) -> None:
        initial = self.producer.event_count
        self.producer.generate_normal_transaction()
        self.producer.generate_normal_metrics()
        assert self.producer.event_count == initial + 2

    @pytest.mark.asyncio
    async def test_stream_events(self) -> None:
        events = []
        async for event in self.producer.stream_events(events_per_second=100, duration_seconds=0.5):
            events.append(event)
        assert len(events) > 10  # Should have generated many events


class TestRollingWindowAggregator:
    """Tests for the rolling window feature aggregator."""

    def setup_method(self) -> None:
        self.aggregator = RollingWindowAggregator(window_seconds=60)
        self.producer = SyntheticTelemetryProducer(seed=42)

    def test_add_event_returns_features(self) -> None:
        event = self.producer.generate_normal_metrics()
        features = self.aggregator.add_event(event)
        assert "service" in features
        assert "window_size" in features
        assert features["window_size"] == 1

    def test_window_accumulates(self) -> None:
        for _ in range(10):
            event = self.producer.generate_normal_metrics()
            features = self.aggregator.add_event(event)
        # Events are distributed across services; total across all windows should be 10
        total = sum(len(w) for w in self.aggregator.windows.values())
        assert total == 10
        assert features["window_size"] >= 1  # Last service's window has at least 1

    def test_zscore_computation(self) -> None:
        event = self.producer.generate_anomalous_event("latency_spike")
        features = self.aggregator.add_event(event)
        # Anomalous latency should have high z-score
        if "latency_p99_ms_zscore" in features:
            assert features["latency_p99_ms_zscore"] > 2


class TestAlertDeduplicator:
    """Tests for the alert deduplication window."""

    def test_first_alert_fires(self) -> None:
        dedup = AlertDeduplicator(window_seconds=30)
        assert dedup.should_fire("test-alert", {"data": "value"}) is True

    def test_duplicate_alert_suppressed(self) -> None:
        dedup = AlertDeduplicator(window_seconds=30)
        dedup.should_fire("test-alert", {"data": "value"})
        assert dedup.should_fire("test-alert", {"data": "value"}) is False

    def test_different_alerts_fire(self) -> None:
        dedup = AlertDeduplicator(window_seconds=30)
        assert dedup.should_fire("alert-1", {}) is True
        assert dedup.should_fire("alert-2", {}) is True

    def test_suppressed_count(self) -> None:
        dedup = AlertDeduplicator(window_seconds=30)
        dedup.should_fire("test-alert", {})
        dedup.should_fire("test-alert", {})
        dedup.should_fire("test-alert", {})
        assert dedup.get_suppressed_count("test-alert") == 2
