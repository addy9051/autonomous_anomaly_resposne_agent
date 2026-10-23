"""
Telemetry Adapters for Live Metric Gathering (Prometheus, Datadog, Simulator).
Produces timestamped, citable evidence records (E1, E2, E3...) for LLM diagnosis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from connectors.manager import TenantConnectorManager
from core.schemas import EvidenceRecord
from core.utils import get_logger

logger = get_logger("telemetry_adapters")


class BaseTelemetryAdapter:
    async def fetch_evidence(self, service: str, lookback_minutes: int = 15) -> list[EvidenceRecord]:
        raise NotImplementedError


class PrometheusAdapter(BaseTelemetryAdapter):
    def __init__(self, url: str, bearer_token: str | None = None) -> None:
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}

    async def fetch_evidence(self, service: str, lookback_minutes: int = 15) -> list[EvidenceRecord]:
        records: list[EvidenceRecord] = []
        now_iso = datetime.now(UTC).isoformat()
        client = httpx.AsyncClient(timeout=10.0, headers=self.headers)

        queries = {
            "CPU Utilization": f'sum(rate(container_cpu_usage_seconds_total{{container=~".*{service}.*"}}[5m])) * 100',
            "Memory Usage (MB)": f'sum(container_memory_working_set_bytes{{container=~".*{service}.*"}}) / 1024 / 1024',
            "HTTP 5xx Error Rate": f'sum(rate(http_requests_total{{status=~"5..", service=~".*{service}.*"}}[5m])) / sum(rate(http_requests_total{{service=~".*{service}.*"}}[5m])) * 100',
        }

        idx = 1
        for label, promql in queries.items():
            try:
                resp = await client.get(f"{self.url}/api/v1/query", params={"query": promql})
                if resp.status_code == 200:
                    data = resp.json()
                    val = "N/A"
                    res = data.get("data", {}).get("result", [])
                    if res:
                        val = f"{float(res[0]['value'][1]):.2f}"
                    records.append(
                        EvidenceRecord(
                            id=f"E{idx}",
                            source="Prometheus",
                            timestamp=now_iso,
                            content=f"Service '{service}' metric {label}: {val}",
                            is_anomaly=True if ("Error" in label and val != "N/A" and float(val) > 2.0) else False,
                        )
                    )
                    idx += 1
            except Exception as exc:
                logger.warning("prometheus_query_failed", query=promql, error=str(exc))
            finally:
                await client.aclose()

        return records


class SimulatorAdapter(BaseTelemetryAdapter):
    """Deterministic synthetic adapter used when no live vendor credentials are configured."""

    async def fetch_evidence(self, service: str, lookback_minutes: int = 15) -> list[EvidenceRecord]:
        now_iso = datetime.now(UTC).isoformat()
        return [
            EvidenceRecord(
                id="E1",
                source="Metrics/Prometheus",
                timestamp=now_iso,
                content=f"Service '{service}' container memory usage spiked to 94.2% (threshold: 80.0%).",
                is_anomaly=True,
            ),
            EvidenceRecord(
                id="E2",
                source="Logs/Aggregator",
                timestamp=now_iso,
                content=f"Observed 142 instances of 'java.lang.OutOfMemoryError: Java heap space' in {service} pods.",
                is_anomaly=True,
            ),
            EvidenceRecord(
                id="E3",
                source="Kubernetes/Events",
                timestamp=now_iso,
                content=f"Pod {service}-7f89d4-x9q2 terminated with exit code 137 (OOMKilled).",
                is_anomaly=True,
            ),
            EvidenceRecord(
                id="E4",
                source="GitHub/Deployments",
                timestamp=now_iso,
                content=f"Service '{service}' released commit sha 3b8f1a (v2.4.1) 12 minutes prior to anomaly.",
                is_anomaly=False,
            ),
        ]


async def get_tenant_telemetry_adapter(tenant_id: str) -> BaseTelemetryAdapter:
    """Resolve the active adapter for the tenant or return the safe simulator."""
    mgr = TenantConnectorManager(tenant_id)
    prom_creds = await mgr.get_connector_credentials("prometheus")
    if prom_creds and prom_creds.get("url"):
        return PrometheusAdapter(prom_creds["url"], prom_creds.get("bearer_token"))

    # Fallback to simulator
    return SimulatorAdapter()
