"""
Unit tests for Monitoring Agent tools.

Tests each tool independently with mocked external services.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from agents.monitoring.tools.monitoring_tools import (
    ALL_MONITORING_TOOLS,
    anomaly_classifier,
    baseline_compare,
    compute_baseline_result,
    prometheus_query,
)
from data_pipeline.connectors.adapters.base import TelemetrySourceError


class TestPrometheusQueryTool:
    """Prometheus tool must never fabricate data."""

    @pytest.mark.asyncio
    async def test_unreachable_reports_evidence_gap(self) -> None:
        with patch("agents.monitoring.tools.monitoring_tools.httpx.AsyncClient") as client_cls:
            client = client_cls.return_value.__aenter__.return_value
            client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
            data = json.loads(await prometheus_query.ainvoke({"query": "latency_p99"}))
        assert data["evidence_gap"] is True
        assert "error" in data


class TestRegistry:
    def test_fabricated_tools_not_registered(self) -> None:
        names = {t.name for t in ALL_MONITORING_TOOLS}
        assert names == {"prometheus_query", "baseline_compare"}


class TestBaselineCompare:
    """Baseline must come from real history or report a gap."""

    def test_insufficient_samples_is_gap(self) -> None:
        data = json.loads(compute_baseline_result("latency_p99", "svc", 1.0, [1.0] * 5))
        assert data["evidence_gap"] is True

    def test_normal_value(self) -> None:
        samples = [250.0 + (i % 5) for i in range(30)]
        data = json.loads(compute_baseline_result("latency_p99", "svc", 252.0, samples))
        assert abs(data["z_score"]) < 2
        assert data["is_anomalous"] is False
        assert data["baseline"]["samples"] == 30

    def test_anomalous_value(self) -> None:
        samples = [250.0 + (i % 5) for i in range(30)]
        data = json.loads(compute_baseline_result("latency_p99", "svc", 1500.0, samples))
        assert data["is_anomalous"] is True
        assert data["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_adapter_failure_is_gap(self) -> None:
        adapter = AsyncMock()
        adapter.fetch_signals.side_effect = TelemetrySourceError("down")
        with patch("data_pipeline.connectors.adapters.get_telemetry_adapter", return_value=adapter):
            data = json.loads(await baseline_compare.ainvoke({"metric_name": "error_rate", "current_value": 0.1}))
        assert data["evidence_gap"] is True


class TestAnomalyClassifier:
    """Tests for Isolation Forest anomaly classifier."""

    @pytest.mark.asyncio
    async def test_normal_vector(self) -> None:
        # Normal metrics vector
        result = await anomaly_classifier.ainvoke({"metrics_vector": [250.0, 0.02, 45.0, 60.0, 500.0, 0.038]})
        data = json.loads(result)
        assert "is_anomaly" in data
        assert "confidence" in data
        assert 0 <= data["confidence"] <= 1

    @pytest.mark.asyncio
    async def test_anomalous_vector(self) -> None:
        # Extreme anomalous metrics — verify classifier runs and returns valid schema
        result = await anomaly_classifier.ainvoke({"metrics_vector": [5000.0, 0.50, 99.0, 98.0, 50000.0, 0.80]})
        data = json.loads(result)
        assert "is_anomaly" in data
        assert "confidence" in data
        assert 0 <= data["confidence"] <= 1
        assert "isolation_score" in data

    @pytest.mark.asyncio
    async def test_different_vector_lengths(self) -> None:
        # Should handle variable length input
        for length in [3, 6, 10]:
            result = await anomaly_classifier.ainvoke({"metrics_vector": [100.0] * length})
            data = json.loads(result)
            assert "is_anomaly" in data
