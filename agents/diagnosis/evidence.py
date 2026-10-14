"""
Scoped evidence collection for the diagnosis agent.

Queries the configured telemetry adapter per affected service and metric,
and records explicit evidence gaps instead of silently dropping failures.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from data_pipeline.connectors.adapters.base import TelemetrySourceError
from shared.utils import get_logger

if TYPE_CHECKING:
    from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter

logger = get_logger("diagnosis_evidence")

DEFAULT_METRICS = ["latency_p99", "error_rate", "transaction_failure_rate"]
DEFAULT_WINDOW_SECONDS = 900
MAX_SERVICES = 3


async def collect_evidence(
    adapter: BaseTelemetryAdapter,
    services: list[str],
    metric_names: list[str] | None = None,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return (evidence_records, missing_sources) for the alert's scope.

    A source that raises, or a metric that returns no samples, is reported as
    a gap in the form ``<source>:<service>:<metric or *>``.
    """
    metrics = metric_names or DEFAULT_METRICS
    source = adapter.source.value
    evidence: list[dict[str, Any]] = []
    gaps: list[str] = []

    for service in services[:MAX_SERVICES]:
        try:
            records = await adapter.fetch_signals(
                entity=service,
                metric_names=metrics,
                time_window_seconds=window_seconds,
            )
        except TelemetrySourceError as exc:
            logger.warning("evidence_source_failed", source=source, service=service, error=str(exc))
            gaps.append(f"{source}:{service}:*")
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("evidence_unexpected_error", source=source, service=service, error=str(exc))
            gaps.append(f"{source}:{service}:*")
            continue

        seen: set[str] = set()
        for rec in records:
            seen.add(rec.metric_name)
            tags = rec.tags or {}
            simulated = bool(tags.get("simulated") or tags.get("is_mock") or tags.get("simulated_fallback"))
            if tags.get("evidence_gap"):
                gaps.append(f"{source}:{service}:{rec.metric_name}")
            evidence.append(
                {
                    "source": source,
                    "entity": service,
                    "query_or_ref": rec.raw_query or rec.metric_name,
                    "metric_name": rec.metric_name,
                    "observed_at": rec.timestamp.isoformat(),
                    "value": rec.value,
                    "unit": rec.unit,
                    "is_contradictory": False,
                    "simulated": simulated,
                    "description": f"Observed {rec.metric_name}={rec.value} {rec.unit}"
                    + (" [SIMULATED]" if simulated else ""),
                    "tags": tags,
                }
            )

        gaps.extend(f"{source}:{service}:{m}" for m in metrics if m not in seen)

    evidence.sort(key=lambda e: e["observed_at"])
    return evidence, gaps
