"""
Prometheus Telemetry Adapter.

Queries Prometheus HTTP API (/api/v1/query, /api/v1/query_range) using PromQL
and normalizes vector/matrix results into NormalizedTelemetryRecord instances.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import httpx

from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter, TelemetrySourceError
from data_pipeline.connectors.query_templates import resolve_query
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

logger = get_logger("prometheus_adapter")


class PrometheusAdapter(BaseTelemetryAdapter):
    """
    Adapter for querying metrics from Prometheus via PromQL HTTP API.
    """

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(source=TelemetryConnectorSource.PROMETHEUS)
        settings = get_settings()
        self.base_url = (base_url or settings.integration.prometheus_url).rstrip("/")
        self.client = httpx.AsyncClient(timeout=10.0, headers=self._auth_headers(settings))
        logger.info("prometheus_adapter_initialized", base_url=self.base_url)

    @staticmethod
    def _auth_headers(settings: Any) -> dict[str, str]:
        token = settings.integration.prometheus_bearer_token
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def close(self) -> None:
        """Close internal HTTP client."""
        await self.client.aclose()

    async def health_check(self) -> bool:
        """Check if Prometheus server is reachable."""
        try:
            resp = await self.client.get(f"{self.base_url}/-/ready")
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("prometheus_health_check_failed", error=str(exc))
            return False

    async def fetch_signals(
        self,
        entity: str,
        metric_names: list[str],
        time_window_seconds: int = 300,
    ) -> list[NormalizedTelemetryRecord]:
        """Fetch multiple metrics for an entity over the lookback window (range query)."""
        records: list[NormalizedTelemetryRecord] = []
        end = datetime.now(timezone.utc)
        start = end - timedelta(seconds=time_window_seconds)
        step = f"{max(15, time_window_seconds // 30)}s"
        for metric in metric_names:
            templated = resolve_query("prometheus", metric, entity)
            query = templated or f'{metric}{{service="{entity}"}}'
            results = await self.query_signal(
                query=query,
                entity=entity,
                start_time=start,
                end_time=end,
                step=step,
                metric_name=metric if templated else None,
            )
            records.extend(results)
        return records


    async def query_signal(
        self,
        query: str,
        entity: str | None = None,
        tags: dict[str, Any] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        step: str | None = None,
        metric_name: str | None = None,
    ) -> list[NormalizedTelemetryRecord]:
        """Execute a PromQL query (instant or range) and normalize the results."""
        # Dynamically determine the endpoint based on parameters
        is_range_query = all([start_time is not None, end_time is not None, step is not None])
        endpoint = (
            f"{self.base_url}/api/v1/query_range"
            if is_range_query
            else f"{self.base_url}/api/v1/query"
        )
        
        params: dict[str, Any] = {"query": query}

        # Core Fix: Explicitly check for non-None values before adding to params
        if is_range_query and start_time is not None and end_time is not None and step is not None:
            params["start"] = start_time.isoformat()
            params["end"] = end_time.isoformat()
            params["step"] = step

        records: list[NormalizedTelemetryRecord] = []

        try:
            resp = await self.client.get(endpoint, params=params)
            if resp.status_code == 200:
                data = resp.json()
                result_type = data.get("data", {}).get("resultType")
                results = data.get("data", {}).get("result", [])
                
                for item in results:
                    metric_meta = item.get("metric", {})
                    target_entity = entity or metric_meta.get("service") or metric_meta.get("job") or "unknown"
                    record_metric = metric_name or metric_meta.get("__name__", query.split("{")[0])
                    combined_tags = {**metric_meta, **(tags or {})}
                    
                    # Core Fix: Separate extraction flows for matrix vs vector types
                    samples: list[list[Any]] = []
                    if result_type == "matrix":
                        # Multi-sample array response from range queries
                        samples = item.get("values", [])
                    else:
                        # Single-sample response from instant queries
                        single_val = item.get("value")
                        if single_val:
                            samples = [single_val]

                    for sample in samples:
                        # sample structure -> [unix_timestamp, "value_string"]
                        raw_ts = float(sample[0])
                        val = float(sample[1])
                        
                        # Convert Prometheus unix timestamp directly to timezone-aware datetime
                        record_dt = datetime.fromtimestamp(raw_ts, tz=timezone.utc)

                        records.append(
                            NormalizedTelemetryRecord(
                                timestamp=record_dt,
                                entity=target_entity,
                                connector_source=self.source,
                                signal_type=TelemetrySignalType.METRIC,
                                metric_name=record_metric,
                                value=val,
                                unit="count" if "count" in record_metric else "value",
                                severity=Severity.HIGH if val > 100.0 else Severity.LOW,
                                tags=combined_tags,
                                raw_query=query,
                            )
                        )
            else:
                logger.warning("prometheus_query_non_200", status=resp.status_code)
                raise TelemetrySourceError(f"Prometheus query returned HTTP {resp.status_code}")
        except httpx.HTTPError as exc:
            logger.warning("prometheus_query_error", query=query, error=str(exc))
            raise TelemetrySourceError(f"Prometheus query failed: {exc}") from exc
        except (ValueError, TypeError, IndexError) as exc:
            logger.warning("prometheus_parse_error", query=query, error=str(exc))
            raise TelemetrySourceError(f"Prometheus response could not be parsed: {exc}") from exc

        return records


    async def stream_signals(
        self,
        poll_interval_seconds: float = 5.0,
        entities: list[str] | None = None,
    ) -> AsyncGenerator[NormalizedTelemetryRecord, None]:
        """Poll Prometheus on an interval and yield normalized signals."""
        queries = [
            'rate(http_requests_total{status=~"5.."}[1m])',
            "container_cpu_usage_seconds_total",
        ]
        
        while True:
            for q in queries:
                try:
                    records = await self.query_signal(query=q)
                except TelemetrySourceError:
                    continue
                for rec in records:
                    if not entities or rec.entity in entities:
                        yield rec
            await asyncio.sleep(poll_interval_seconds)
