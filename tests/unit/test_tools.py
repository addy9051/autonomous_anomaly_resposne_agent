"""
Unit tests for Monitoring Agent tools.

Tests each tool independently with mocked external services.
"""

from __future__ import annotations

import json

import pytest

from agents.monitoring.tools.monitoring_tools import (
    anomaly_classifier,
    baseline_compare,
    fraud_signal_fetch,
    kafka_lag_inspector,
    prometheus_query,
)


class TestPrometheusQueryTool:
    """Tests for Prometheus query tool."""

    @pytest.mark.asyncio
    async def test_returns_valid_json(self) -> None:
        result = await prometheus_query.ainvoke(
            {"query": "http_request_duration_seconds", "time_range": "5m"}
        )
        data = json.loads(result)
        assert isinstance(data, list)
        assert len(data) > 0
        assert "value" in data[0]

    @pytest.mark.asyncio
    async def test_latency_query(self) -> None:
        result = await prometheus_query.ainvoke({"query": "latency_p99"})
        data = json.loads(result)
        assert len(data) > 0
        assert data[0]["value"] > 0

    @pytest.mark.asyncio
    async def test_error_query(self) -> None:
        result = await prometheus_query.ainvoke({"query": "error_rate"})
        data = json.loads(result)
        assert len(data) > 0


class TestKafkaLagInspector:
    """Tests for Kafka lag inspector tool."""

    @pytest.mark.asyncio
    async def test_returns_lag_info(self) -> None:
        result = await kafka_lag_inspector.ainvoke({
            "consumer_group": "test-group",
            "topic": "test-topic"
        })
        data = json.loads(result)
        assert "total_lag" in data
        assert "partitions" in data
        assert "status" in data
        assert data["consumer_group"] == "test-group"

    @pytest.mark.asyncio
    async def test_partitions_have_lag(self) -> None:
        result = await kafka_lag_inspector.ainvoke({})
        data = json.loads(result)
        for partition in data["partitions"]:
            assert "partition" in partition
            assert "lag" in partition
            assert partition["lag"] >= 0


class TestFraudSignalFetch:
    """Tests for fraud signal fetch tool."""

    @pytest.mark.asyncio
    async def test_returns_fraud_stats(self) -> None:
        result = await fraud_signal_fetch.ainvoke({
            "service": "payment-gateway",
            "window_minutes": 5
        })
        data = json.loads(result)
        assert "fraud_scores" in data
        assert "mean" in data["fraud_scores"]
        assert "p95" in data["fraud_scores"]
        assert data["total_transactions"] > 0

    @pytest.mark.asyncio
    async def test_fraud_scores_valid_range(self) -> None:
        result = await fraud_signal_fetch.ainvoke({})
        data = json.loads(result)
        scores = data["fraud_scores"]
        assert 0 <= scores["mean"] <= 1
        assert 0 <= scores["p95"] <= 1


class TestBaselineCompare:
    """Tests for baseline compare tool."""

    @pytest.mark.asyncio
    async def test_normal_value(self) -> None:
        result = await baseline_compare.ainvoke({
            "metric_name": "p99_latency_ms",
            "current_value": 260.0,  # Close to normal
        })
        data = json.loads(result)
        assert "z_score" in data
        assert "is_anomalous" in data
        assert abs(data["z_score"]) < 2  # Should be normal

    @pytest.mark.asyncio
    async def test_anomalous_value(self) -> None:
        result = await baseline_compare.ainvoke({
            "metric_name": "p99_latency_ms",
            "current_value": 1500.0,  # Way above baseline
        })
        data = json.loads(result)
        assert data["is_anomalous"] is True
        assert data["z_score"] > 2

    @pytest.mark.asyncio
    async def test_severity_classification(self) -> None:
        result = await baseline_compare.ainvoke({
            "metric_name": "error_rate",
            "current_value": 0.15,  # Very high error rate
        })
        data = json.loads(result)
        assert data["severity"] in ["critical", "high", "medium", "low"]


class TestAnomalyClassifier:
    """Tests for Isolation Forest anomaly classifier."""

    @pytest.mark.asyncio
    async def test_normal_vector(self) -> None:
        # Normal metrics vector
        result = await anomaly_classifier.ainvoke({
            "metrics_vector": [250.0, 0.02, 45.0, 60.0, 500.0, 0.038]
        })
        data = json.loads(result)
        assert "is_anomaly" in data
        assert "confidence" in data
        assert 0 <= data["confidence"] <= 1

    @pytest.mark.asyncio
    async def test_anomalous_vector(self) -> None:
        # Extreme anomalous metrics — verify classifier runs and returns valid schema
        result = await anomaly_classifier.ainvoke({
            "metrics_vector": [5000.0, 0.50, 99.0, 98.0, 50000.0, 0.80]
        })
        data = json.loads(result)
        assert "is_anomaly" in data
        assert "confidence" in data
        assert 0 <= data["confidence"] <= 1
        assert "isolation_score" in data

    @pytest.mark.asyncio
    async def test_different_vector_lengths(self) -> None:
        # Should handle variable length input
        for length in [3, 6, 10]:
            result = await anomaly_classifier.ainvoke({
                "metrics_vector": [100.0] * length
            })
            data = json.loads(result)
            assert "is_anomaly" in data
