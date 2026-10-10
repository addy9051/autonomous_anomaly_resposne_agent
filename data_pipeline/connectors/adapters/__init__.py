"""
Universal Telemetry Adapters Package.

Standardized adapters for Observability Platform APIs:
- SimulatorAdapter
- PrometheusAdapter
- DatadogAdapter
- SplunkAdapter
"""

from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter
from data_pipeline.connectors.adapters.datadog_adapter import DatadogAdapter
from data_pipeline.connectors.adapters.factory import (
    get_telemetry_adapter,
    list_available_adapters,
)
from data_pipeline.connectors.adapters.prometheus_adapter import PrometheusAdapter
from data_pipeline.connectors.adapters.simulator_adapter import SimulatorAdapter
from data_pipeline.connectors.adapters.splunk_adapter import SplunkAdapter

__all__ = [
    "BaseTelemetryAdapter",
    "DatadogAdapter",
    "PrometheusAdapter",
    "SimulatorAdapter",
    "SplunkAdapter",
    "get_telemetry_adapter",
    "list_available_adapters",
]
