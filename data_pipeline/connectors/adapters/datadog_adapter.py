"""
Datadog Telemetry Adapter.

Queries Datadog REST API (/api/v1/query) for infrastructure and application metrics,
normalizing series data into NormalizedTelemetryRecord instances.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx

from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter
from shared.config import get_settings
from shared.schemas import (
    NormalizedTelemetryRecord,
    Severity,
    TelemetryConnectorSource,
    TelemetrySignalType,
)
from shared.utils import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = get_logger("datadog_adapter")


class DatadogAdapter(BaseTelemetryAdapter):
    """
    Adapter for querying metrics and monitor states from Datadog API.
    """

    def __init__(
        self,
        api_key: str | None = None,
        app_key: str | None = None,
        site: str | None = None,
    ) -> None:
        super().__init__(source=TelemetryConnectorSource.DATADOG)
        settings = get_settings()
        self.api_key = api_key or settings.integration.datadog_api_key
        self.app_key = app_key or settings.integration.datadog_app_key
        self.site = site or settings.integration.datadog_site
        self.base_url = f"https://api.{self.site}".rstrip("/")
        self.client = httpx.AsyncClient(timeout=10.0)
        logger.info("datadog_adapter_initialized", site=self.site, configured=bool(self.api_key))

    async def close(self) -> None:
        """Close internal HTTP client."""
        await self.client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "DD-API-KEY": self.api_key,
            "DD-APPLICATION-KEY": self.app_key,
            "Accept": "application/json",
        }

    async def health_check(self) -> bool:
        """Validate Datadog API key authentication."""
        if not self.api_key:
            return False
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/v1/validate",
                headers=self._headers,
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("datadog_health_check_failed", error=str(exc))
            return False

    async def fetch_signals(
        self,
        entity: str,
        metric_names: list[str],
        time_window_seconds: int = 300,
    ) -> list[NormalizedTelemetryRecord]:
        """Fetch metric timeseries for an entity via Datadog Query API."""
        records: list[NormalizedTelemetryRecord] = []
        for metric in metric_names:
            query = f"avg:{metric}{{service:{entity}}}"
            records.extend(await self.query_signal(query=query, entity=entity))
        return records

    
    
    async def query_signal(
        self,
        query: str,
        entity: str | None = None,
        tags: dict[str, Any] | None = None,
    ) -> list[NormalizedTelemetryRecord]:
        """Query Datadog v2 API for modern metric series evaluation."""
        # Upgrading to the recommended V2 API path for production parity
        endpoint = f"{self.base_url}/api/v2/query/timeseries"
        records: list[NormalizedTelemetryRecord] = []
        
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - (300 * 1000) # 5 minutes window

        if not self.api_key:
            logger.debug("datadog_api_key_not_configured_using_mock", query=query)
            return [
                NormalizedTelemetryRecord(
                    timestamp=datetime.now(timezone.utc),
                    entity=entity or "payment-service-prod",
                    connector_source=self.source,
                    signal_type=TelemetrySignalType.METRIC,
                    metric_name=query.split("{")[0],
                    value=2.45,
                    baseline_mean=0.15,
                    severity=Severity.HIGH,
                    tags={**(tags or {}), "simulated_mock": True},
                    raw_query=query,
                )
            ]

        # V2 Metrics API expects a clean JSON POST payload rather than raw query params
        payload = {
            "data": {
                "type": "timeseries_request",
                "attributes": {
                    "from": from_ms,
                    "to": now_ms,
                    "queries": [
                        {
                            "data_source": "metrics",
                            "query": query
                        }
                    ]
                }
            }
        }

        try:
            resp = await self.client.post(
                endpoint,
                headers=self._headers,
                json=payload,
            )
            
            if resp.status_code == 200:
                data = resp.json()
                series_list = data.get("data", {}).get("attributes", {}).get("series", [])
                
                for series in series_list:
                    # In V2, timeseries data arrays map coordinates directly 
                    values = series.get("values", [])
                    times = data.get("data", {}).get("attributes", {}).get("times", [])
                    
                    metric_name = series.get("query_index", query)
                    series_tags = series.get("tags", [])

                    # Extract the latest value cleanly from the arrays
                    latest_val = float(values[-1]) if values else 0.0
                    all_vals = [float(v) for v in values if v is not None]
                    baseline = sum(all_vals) / len(all_vals) if all_vals else latest_val
                    
                    # Extract raw timestamp context directly from Datadog API timeline
                    raw_ts_ms = times[-1] if times else now_ms
                    record_dt = datetime.fromtimestamp(raw_ts_ms / 1000.0, tz=timezone.utc)

                    # Safe parsing block protecting against both flag tags and key-value tags
                    parsed_tags = {}
                    for t in series_tags:
                        if ":" in t:
                            k, v = t.split(":", 1)
                            parsed_tags[k] = v
                        else:
                            parsed_tags[t] = True

                    combined_tags = {**parsed_tags, **(tags or {})}

                    records.append(
                        NormalizedTelemetryRecord(
                            timestamp=record_dt,
                            entity=entity or parsed_tags.get("service", "datadog_scope"),
                            connector_source=self.source,
                            signal_type=TelemetrySignalType.METRIC,
                            metric_name=metric_name,
                            value=round(latest_val, 2),
                            baseline_mean=round(baseline, 2),
                            tags=combined_tags,
                            raw_query=query,
                        )
                    )
            else:
                logger.warning("datadog_query_failed", status=resp.status_code, text=resp.text)
        except Exception as exc:
            logger.warning("datadog_query_exception", error=str(exc))

        return records


    async def stream_signals(
        self,
        poll_interval_seconds: float = 5.0,
        entities: list[str] | None = None,
    ) -> AsyncGenerator[NormalizedTelemetryRecord, None]:
        """Poll Datadog on an interval and yield normalized signals."""
        queries = ["avg:trace.rack.request.errors{*}", "avg:system.cpu.user{*}"]
        async def _run() -> AsyncGenerator[NormalizedTelemetryRecord, None]:
            while True:
                for q in queries:
                    records = await self.query_signal(query=q)
                    for rec in records:
                        if not entities or rec.entity in entities:
                            yield rec
                await asyncio.sleep(poll_interval_seconds)
        return _run()
