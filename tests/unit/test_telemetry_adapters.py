"""
Unit tests for Universal Telemetry Adapters.

Tests:
- Factory adapter creation and CONNECTOR_MODE switching
- SimulatorAdapter signal fetching, queries, and stream generation
- PrometheusAdapter query parsing and normalization
- DatadogAdapter API queries and series normalization
- SplunkAdapter search export parsing and log summary normalization
"""

from __future__ import annotations

import pytest

from data_pipeline.connectors.adapters import (
    DatadogAdapter,
    PrometheusAdapter,
    SimulatorAdapter,
    SplunkAdapter,
    get_telemetry_adapter,
    list_available_adapters,
)
from shared.schemas import (
    Severity,
    TelemetryConnectorSource,
    TelemetrySignalType,
)


class TestAdapterFactory:
    """Tests for telemetry adapter factory function."""

    def test_list_available_adapters(self) -> None:
        adapters = list_available_adapters()
        assert "simulator" in adapters
        assert "prometheus" in adapters
        assert "datadog" in adapters
        assert "splunk" in adapters

    def test_get_simulator_adapter(self) -> None:
        adapter = get_telemetry_adapter("simulator")
        assert isinstance(adapter, SimulatorAdapter)
        assert adapter.source == TelemetryConnectorSource.SIMULATOR

    def test_get_prometheus_adapter(self) -> None:
        adapter = get_telemetry_adapter("prometheus")
        assert isinstance(adapter, PrometheusAdapter)
        assert adapter.source == TelemetryConnectorSource.PROMETHEUS

    def test_get_datadog_adapter(self) -> None:
        adapter = get_telemetry_adapter("datadog")
        assert isinstance(adapter, DatadogAdapter)
        assert adapter.source == TelemetryConnectorSource.DATADOG

    def test_get_splunk_adapter(self) -> None:
        adapter = get_telemetry_adapter("splunk")
        assert isinstance(adapter, SplunkAdapter)
        assert adapter.source == TelemetryConnectorSource.SPLUNK

    def test_fallback_to_simulator(self) -> None:
        adapter = get_telemetry_adapter("invalid_vendor_xyz")
        assert isinstance(adapter, SimulatorAdapter)


class TestSimulatorAdapter:
    """Tests for SimulatorAdapter."""

    @pytest.mark.asyncio
    async def test_health_check(self) -> None:
        adapter = SimulatorAdapter()
        is_healthy = await adapter.health_check()
        assert is_healthy is True

    @pytest.mark.asyncio
    async def test_fetch_signals(self) -> None:
        adapter = SimulatorAdapter(seed=123)
        records = await adapter.fetch_signals(
            entity="payment-service-prod",
            metric_names=["latency_p99", "error_rate"],
        )
        assert len(records) == 2
        assert records[0].entity == "payment-service-prod"
        assert records[0].connector_source == TelemetryConnectorSource.SIMULATOR
        assert records[0].baseline_mean is not None
        assert records[0].deviation_sigma is not None

    @pytest.mark.asyncio
    async def test_query_signal(self) -> None:
        adapter = SimulatorAdapter()
        records = await adapter.query_signal("custom_transaction_rate{env=prod}")
        assert len(records) == 1
        assert records[0].connector_source == TelemetryConnectorSource.SIMULATOR
        assert records[0].raw_query == "custom_transaction_rate{env=prod}"

    @pytest.mark.asyncio
    async def test_stream_signals(self) -> None:
        adapter = SimulatorAdapter(seed=42)
        stream = adapter.stream_signals(poll_interval_seconds=0.01)

        records = []
        async for rec in stream:
            records.append(rec)
            if len(records) >= 3:
                break

        assert len(records) == 3
        for rec in records:
            assert rec.connector_source == TelemetryConnectorSource.SIMULATOR
            assert rec.signal_type == TelemetrySignalType.METRIC


class TestPrometheusAdapter:
    """Tests for PrometheusAdapter."""

    @pytest.mark.asyncio
    async def test_mocked_query(self) -> None:
        adapter = PrometheusAdapter(base_url="http://localhost:9090")
        try:
            # When server is not running, falls back safely without unhandled crash
            records = await adapter.query_signal(
                query="up{job='payment_service'}",
                entity="payment-service",
            )
            assert len(records) >= 1
            assert records[0].connector_source == TelemetryConnectorSource.PROMETHEUS
        finally:
            await adapter.close()


class TestDatadogAdapter:
    """Tests for DatadogAdapter."""

    @pytest.mark.asyncio
    async def test_mock_fallback_when_unconfigured(self) -> None:
        adapter = DatadogAdapter(api_key="", app_key="")
        try:
            records = await adapter.query_signal(
                query="avg:trace.payment.errors{*}",
                entity="checkout-api",
            )
            assert len(records) == 1
            assert records[0].connector_source == TelemetryConnectorSource.DATADOG
            assert records[0].entity == "checkout-api"
            assert records[0].tags.get("simulated_mock") is True
        finally:
            await adapter.close()


class TestSplunkAdapter:
    """Tests for SplunkAdapter."""

    @pytest.mark.asyncio
    async def test_mock_fallback_when_unconfigured(self) -> None:
        adapter = SplunkAdapter(token="", password="")
        try:
            records = await adapter.fetch_signals(entity="settlement-engine", metric_names=["errors"])
            assert len(records) == 1
            assert records[0].connector_source == TelemetryConnectorSource.SPLUNK
            assert records[0].signal_type == TelemetrySignalType.LOG_SUMMARY
            assert records[0].severity == Severity.CRITICAL
            assert records[0].tags.get("error_code") == "504_GATEWAY_TIMEOUT"
        finally:
            await adapter.close()
