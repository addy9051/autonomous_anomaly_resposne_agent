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
    if mode is None:
        modes = get_active_modes()
        if len(modes) > 1:
            from data_pipeline.connectors.adapters.composite import CompositeTelemetryAdapter

            logger.info("loading_composite_telemetry_adapter", modes=modes)
            return CompositeTelemetryAdapter([get_telemetry_adapter(m) for m in modes])
        mode = modes[0]
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


def get_active_modes() -> list[str]:
    """Return the configured vendor list: CONNECTOR_MODES if set, else the single connector mode."""
    cfg = get_settings().integration
    raw = cfg.telemetry_connector_modes.strip()
    modes = [m.strip().lower() for m in raw.split(",") if m.strip()] if raw else [cfg.telemetry_connector_mode.lower().strip()]
    return list(dict.fromkeys(modes))


def validate_modes(modes: list[str]) -> None:
    """Fail fast on unknown vendors or vendors listed without credentials."""
    cfg = get_settings().integration
    missing: list[str] = []
    for mode in modes:
        if mode in ("datadog", "dd") and not (cfg.datadog_api_key and cfg.datadog_app_key):
            missing.append("datadog (DATADOG_API_KEY / DATADOG_APP_KEY)")
        elif mode == "splunk" and not (cfg.splunk_token or cfg.splunk_password):
            missing.append("splunk (SPLUNK_TOKEN or SPLUNK_PASSWORD)")
        elif mode in ("prometheus", "prom") and not cfg.prometheus_url:
            missing.append("prometheus (PROMETHEUS_URL)")
        elif mode not in ("simulator", "sim", "mock", "prometheus", "prom", "datadog", "dd", "splunk"):
            raise ValueError(f"Unknown telemetry connector mode '{mode}'. Supported: {', '.join(list_available_adapters())}")
    if missing:
        raise ValueError("Connector modes listed without credentials: " + ", ".join(missing))

