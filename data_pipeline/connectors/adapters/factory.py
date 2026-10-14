"""
Telemetry Adapter Factory & Registry.

Provides dynamic switching between telemetry connector implementations
based on environment configuration (CONNECTOR_MODE) or programmatic selection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from data_pipeline.connectors.adapters.datadog_adapter import DatadogAdapter
from data_pipeline.connectors.adapters.prometheus_adapter import PrometheusAdapter
from data_pipeline.connectors.adapters.simulator_adapter import SimulatorAdapter
from data_pipeline.connectors.adapters.splunk_adapter import SplunkAdapter
from shared.config import get_settings
from shared.utils import get_logger

if TYPE_CHECKING:
    from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter

logger = get_logger("adapter_factory")


def get_telemetry_adapter(mode: str | None = None) -> BaseTelemetryAdapter:
    """
    Factory function to retrieve the configured telemetry adapter.

    Args:
        mode: Explicit adapter name ("simulator", "prometheus", "datadog", "splunk").
              If None, defaults to `settings.integration.telemetry_connector_mode`.

    Returns:
        Configured BaseTelemetryAdapter instance.
    """
    settings = get_settings()
    active_mode = (mode or settings.integration.telemetry_connector_mode).lower().strip()

    logger.info("loading_telemetry_adapter", mode=active_mode)

    if active_mode in ["simulator", "sim", "mock"]:
        return SimulatorAdapter()
    elif active_mode in ["prometheus", "prom"]:
        return PrometheusAdapter()
    elif active_mode in ["datadog", "dd"]:
        return DatadogAdapter()
    elif active_mode in ["splunk"]:
        return SplunkAdapter()
    else:
        raise ValueError(
            f"Unknown telemetry connector mode '{active_mode}'. "
            f"Supported: {', '.join(list_available_adapters())}"
        )


def list_available_adapters() -> list[str]:
    """Return list of supported telemetry connector adapter names."""
    return ["simulator", "prometheus", "datadog", "splunk"]
