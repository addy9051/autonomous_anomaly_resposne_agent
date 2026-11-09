"""
Scoped evidence collection for the diagnosis agent.

Queries the configured telemetry adapter per affected service and metric,
and records explicit evidence gaps instead of silently dropping failures.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from agents.diagnosis.onset import condense_evidence
from data_pipeline.connectors import deploy_events
from data_pipeline.connectors.adapters.base import TelemetrySourceError
from shared.utils import get_logger

if TYPE_CHECKING:
    from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter
    from shared.schemas import NormalizedTelemetryRecord

logger = get_logger("diagnosis_evidence")

DEFAULT_METRICS = ["latency_p99", "error_rate", "transaction_failure_rate"]
DEFAULT_WINDOW_SECONDS = 900
# Dating a symptom needs samples from before it started. 900 s is too short for that: in the benchmark only 7% of
# onsets have a baseline inside it, against 87% for 3600 s and 100% for 5400 s (docs/pivot/08).
SYMPTOM_WINDOW_SECONDS = 5400
DEPLOY_WINDOW_SECONDS = 6 * 3600
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
            if (rec.tags or {}).get("evidence_gap"):
                gaps.append(f"{source}:{service}:{rec.metric_name}")
            evidence.append(record_to_evidence(rec, entity=service))

        gaps.extend(f"{source}:{service}:{m}" for m in metrics if m not in seen)

    evidence.sort(key=_sort_key)
    return evidence, gaps


def record_to_evidence(rec: NormalizedTelemetryRecord, entity: str | None = None) -> dict[str, Any]:
    """Convert a normalized record to an evidence dict carrying full provenance."""
    return {
        "source": rec.connector_source.value,
        "entity": entity or rec.entity,
        "query_or_ref": rec.source_ref or rec.raw_query or rec.metric_name,
        "metric_name": rec.metric_name,
        "signal_type": rec.signal_type.value,
        "observed_at": rec.timestamp.isoformat(),
        "event_time": rec.timestamp.isoformat(),
        "ingested_at": rec.ingested_at.isoformat(),
        "freshness_seconds": rec.freshness_seconds,
        "value": rec.value,
        "unit": rec.unit,
        "is_contradictory": False,
        "simulated": rec.is_simulated,
        "description": f"Observed {rec.metric_name}={rec.value} {rec.unit}".strip()
        + (" [SIMULATED]" if rec.is_simulated else ""),
        "tags": rec.tags or {},
    }


def _sort_key(e: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(e["observed_at"])


async def collect_symptom_evidence(
    adapter: BaseTelemetryAdapter,
    services: list[str],
    metric_names: list[str] | None = None,
    window_seconds: int = SYMPTOM_WINDOW_SECONDS,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Symptom evidence dated for the temporal rule: one record per metric, at the time it left its baseline.

    Uses a longer lookback than ``collect_evidence`` so there are baseline samples before the symptom, then
    condenses each series (see ``agents.diagnosis.onset``). Only adapters that return the whole series help: the
    Prometheus adapter does; the Datadog adapter returns the latest value and a mean, and the Splunk adapter
    returns aggregates, so for those the onset stays unknown and the rule rules nothing out.
    """
    evidence, gaps = await collect_evidence(adapter, services, metric_names, window_seconds)
    return condense_evidence(evidence), gaps


async def collect_deploy_evidence(
    window_seconds: int = DEPLOY_WINDOW_SECONDS,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Recent deployments as 'what changed' evidence. No-op when not configured."""
    if not deploy_events.is_configured():
        return [], []
    try:
        records = await deploy_events.fetch_recent_deployments(window_seconds)
    except TelemetrySourceError as exc:
        logger.warning("deploy_events_failed", error=str(exc))
        return [], ["github:deployments"]
    evidence = []
    for rec in records:
        item = record_to_evidence(rec)
        item["description"] = (
            f"Deployment of {rec.tags.get('ref', '?')} ({str(rec.tags.get('sha', ''))[:8]}) "
            f"by {rec.tags.get('creator') or 'unknown'} to {rec.environment}"
        )
        evidence.append(item)
    return evidence, []
