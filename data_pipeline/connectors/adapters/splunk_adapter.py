"""
Splunk Telemetry Adapter.

Queries Splunk REST Search API (/services/search/jobs/export) for aggregated
log counts, transaction failures, and error distributions, normalizing outputs
into NormalizedTelemetryRecord instances with signal_type=LOG_SUMMARY.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import json

import httpx

from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter, TelemetrySourceError
from data_pipeline.connectors.query_templates import resolve_query
from shared.config import get_settings
from shared.egress import guarded_async_client
from shared.schemas import (
    NormalizedTelemetryRecord,
    Severity,
    TelemetryConnectorSource,
    TelemetrySignalType,
)
from shared.utils import get_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = get_logger("splunk_adapter")


class SplunkAdapter(BaseTelemetryAdapter):
    """
    Adapter for querying log aggregations from Splunk REST Search API.
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        super().__init__(source=TelemetryConnectorSource.SPLUNK)
        settings = get_settings()
        self.host = host or settings.integration.splunk_host
        self.port = port or settings.integration.splunk_port
        self.token = token or settings.integration.splunk_token
        self.username = username or settings.integration.splunk_username
        self.password = password or settings.integration.splunk_password
        self.base_url = f"https://{self.host}:{self.port}".rstrip("/")
        self.client = guarded_async_client(verify=self._tls_verify(settings), timeout=15.0)
        logger.info("splunk_adapter_initialized", host=self.host, port=self.port, configured=bool(self.token or self.password))

    @staticmethod
    def _tls_verify(settings: Any) -> bool | str:
        cfg = settings.integration
        if cfg.splunk_ca_bundle:
            return cfg.splunk_ca_bundle
        return bool(cfg.splunk_verify_ssl)

    async def close(self) -> None:
        """Close internal HTTP client."""
        await self.client.aclose()

    @property
    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @property
    def _auth(self) -> tuple[str, str] | None:
        if not self.token and self.username and self.password:
            return (self.username, self.password)
        return None

    async def health_check(self) -> bool:
        """Verify Splunk server connectivity."""
        if not (self.token or self.password):
            return False
        try:
            resp = await self.client.get(
                f"{self.base_url}/services/server/info?output_mode=json",
                headers=self._headers,
                auth=self._auth,
            )
            return resp.status_code == 200
        except Exception as exc:
            logger.warning("splunk_health_check_failed", error=str(exc))
            return False

    async def fetch_signals(
        self,
        entity: str,
        metric_names: list[str],
        time_window_seconds: int = 300,
    ) -> list[NormalizedTelemetryRecord]:
        """Fetch canonical metrics (templated searches) or the legacy error-log summary for an entity."""
        records: list[NormalizedTelemetryRecord] = []
        templated_any = False
        for metric in metric_names:
            query = resolve_query("splunk", metric, entity)
            if query is None:
                continue
            templated_any = True
            records.extend(
                await self.query_signal(
                    query=query,
                    entity=entity,
                    time_window_seconds=time_window_seconds,
                    metric_name=metric,
                )
            )
        if templated_any:
            return records
        search_query = (
            f"search index=* service={entity} status>=500 "
            f"| stats count as failure_count by error_code"
        )
        return await self.query_signal(
            query=search_query, entity=entity, time_window_seconds=time_window_seconds
        )

    async def query_signal(
        self,
        query: str,
        entity: str | None = None,
        tags: dict[str, Any] | None = None,
        time_window_seconds: int = 300,
        metric_name: str | None = None,
    ) -> list[NormalizedTelemetryRecord]:
        """Execute a Splunk search export and return normalized log summary records."""
        endpoint = f"{self.base_url}/services/search/jobs/export"
        records: list[NormalizedTelemetryRecord] = []

        if not (self.token or self.password):
            raise TelemetrySourceError("Splunk credentials are not configured")

        try:
            search_param = query if query.startswith("search ") else f"search {query}"
            
            # Formulate parameters to ensure time boundary controls (e.g. past 5 mins)
            data_payload = {
                "search": search_param, 
                "output_mode": "json",
                "earliest_time": f"-{int(time_window_seconds)}s",
                "latest_time": "now"
            }

            # Build request keyword arguments dynamically
            kwargs: dict[str, Any] = {
                "headers": self._headers,
                "data": data_payload,
            }
            
            # Type-Safe Check: Only bind auth if it is an active tuple
            active_auth = self._auth
            if active_auth is not None:
                kwargs["auth"] = active_auth

            resp = await self.client.post(
                endpoint,
                **kwargs,
            )
            
            if resp.status_code == 200:
                # Splunk export streams valid JSON lines string rows
                for line in resp.text.splitlines():
                    if not line.strip():
                        continue
                    try:
                        # Clean public native Python JSON parsing flow
                        entry = json.loads(line)
                        result = entry.get("result", {})
                        if not result:
                            continue
                            
                        if metric_name:
                            raw_val = result.get(metric_name)
                            if raw_val in (None, ""):
                                continue
                            records.append(
                                NormalizedTelemetryRecord(
                                    timestamp=datetime.now(timezone.utc),
                                    entity=entity or result.get("service", "unknown"),
                                    connector_source=self.source,
                                    signal_type=TelemetrySignalType.METRIC,
                                    metric_name=metric_name,
                                    value=float(raw_val),
                                    tags=dict(tags or {}),
                                    raw_query=query,
                                )
                            )
                            continue

                        # Dynamic value lookup strategy: handles structural changes 
                        # in your `stats count as X` alias automatically.
                        if metric_name:
                            raw_val = result.get(metric_name)
                            if raw_val in (None, ""):
                                continue
                            records.append(
                                NormalizedTelemetryRecord(
                                    timestamp=datetime.now(timezone.utc),
                                    entity=entity or result.get("service", "unknown"),
                                    connector_source=self.source,
                                    signal_type=TelemetrySignalType.METRIC,
                                    metric_name=metric_name,
                                    value=float(raw_val),
                                    tags=dict(tags or {}),
                                    raw_query=query,
                                )
                            )
                            continue

                        count_val = 1.0
                        for target_key in ["count", "failure_count", "sum"]:
                            if target_key in result:
                                count_val = float(result[target_key])
                                break
                        else:
                            # Fallback selector checking if an arbitrary custom aggregation name exists
                            remaining_keys = [k for k in result.keys() if k not in ["error_code", "service"]]
                            if remaining_keys:
                                count_val = float(result[remaining_keys[0]])

                        err_code = result.get("error_code", "UNKNOWN")

                        records.append(
                            NormalizedTelemetryRecord(
                                timestamp=datetime.now(timezone.utc),
                                entity=entity or result.get("service", "unknown"),
                                connector_source=self.source,
                                signal_type=TelemetrySignalType.LOG_SUMMARY,
                                metric_name="error_log_count",
                                value=count_val,
                                unit="count",
                                severity=Severity.HIGH if count_val > 20 else Severity.LOW,
                                tags={**(tags or {}), "error_code": err_code},
                                raw_query=query,
                            )
                        )
                    except Exception:
                        continue
            else:
                logger.warning("splunk_query_failed", status=resp.status_code)
                raise TelemetrySourceError(f"Splunk query returned HTTP {resp.status_code}")
        except httpx.HTTPError as exc:
            logger.warning("splunk_query_exception", error=str(exc))
            raise TelemetrySourceError(f"Splunk query failed: {exc}") from exc

        return records

    async def stream_signals(
        self,
        poll_interval_seconds: float = 5.0,
        entities: list[str] | None = None,
    ) -> AsyncGenerator[NormalizedTelemetryRecord, None]:
        """Poll Splunk for error log spikes on an interval."""

        default_query = "search index=main (status>=500 OR level=ERROR) | stats count by service"
        
        while True:
            try:
                records = await self.query_signal(query=default_query)
            except TelemetrySourceError:
                records = []
            for rec in records:
                if not entities or rec.entity in entities:
                    yield rec
            await asyncio.sleep(poll_interval_seconds)
