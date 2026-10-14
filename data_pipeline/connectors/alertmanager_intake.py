"""
Alertmanager webhook intake.

Converts Prometheus Alertmanager webhook (v4) payloads into scoped AnomalyEvents so an
alert, not a continuous telemetry stream, triggers an investigation. Provenance
(fingerprint, generator URL, labels, annotations) is preserved in `raw_event`.
"""

from __future__ import annotations

from typing import Any

from shared.schemas import AnomalyEvent, AnomalyType, MetricsSnapshot, Severity

_SEVERITY_MAP = {
    "critical": Severity.CRITICAL,
    "page": Severity.CRITICAL,
    "high": Severity.HIGH,
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "medium": Severity.MEDIUM,
    "info": Severity.LOW,
    "low": Severity.LOW,
}

# Ordered: first keyword match wins.
_TYPE_KEYWORDS: list[tuple[tuple[str, ...], AnomalyType]] = [
    (("latency", "slow", "duration", "p99", "p95"), AnomalyType.LATENCY_SPIKE),
    (("error", "5xx", "failure", "failed"), AnomalyType.ERROR_RATE),
    (("cpu", "memory", "disk", "saturation", "oom", "lag"), AnomalyType.RESOURCE_SATURATION),
    (("fraud",), AnomalyType.FRAUD_SIGNAL),
    (("security", "auth", "intrusion"), AnomalyType.SECURITY),
    (("quality", "schema", "stale"), AnomalyType.DATA_QUALITY),
    (("volume", "traffic", "rate"), AnomalyType.VOLUME_ANOMALY),
]

_SERVICE_LABELS = ("service", "service_name", "app", "job", "deployment", "pod", "instance")


def _classify(text: str) -> AnomalyType:
    lowered = text.lower()
    for keywords, anomaly_type in _TYPE_KEYWORDS:
        if any(k in lowered for k in keywords):
            return anomaly_type
    return AnomalyType.ERROR_RATE


def _resolve_service(labels: dict[str, str]) -> str | None:
    for key in _SERVICE_LABELS:
        value = labels.get(key)
        if value:
            return value
    return None


def parse_alertmanager_payload(payload: dict[str, Any]) -> list[AnomalyEvent]:
    """Return one AnomalyEvent per *firing* alert that names an affected service.

    Resolved alerts and alerts without any service-identifying label are skipped:
    an investigation must be scoped to a concrete entity.
    """
    events: list[AnomalyEvent] = []
    for alert in payload.get("alerts", []) or []:
        if alert.get("status") != "firing":
            continue
        labels: dict[str, str] = alert.get("labels") or {}
        annotations: dict[str, str] = alert.get("annotations") or {}
        service = _resolve_service(labels)
        if not service:
            continue

        alertname = labels.get("alertname", "unknown_alert")
        summary = annotations.get("summary") or annotations.get("description") or alertname
        severity = _SEVERITY_MAP.get(labels.get("severity", "").lower(), Severity.MEDIUM)

        events.append(
            AnomalyEvent(
                severity=severity,
                affected_services=[service],
                anomaly_type=_classify(f"{alertname} {summary}"),
                metrics_snapshot=MetricsSnapshot(),
                reasoning=f"Alertmanager alert '{alertname}' fired: {summary}",
                # Alert rule evaluation is deterministic; confidence reflects that the alert fired.
                confidence=1.0,
                raw_event={
                    "intake_source": "alertmanager",
                    "fingerprint": alert.get("fingerprint"),
                    "alertname": alertname,
                    "labels": labels,
                    "annotations": annotations,
                    "starts_at": alert.get("startsAt"),
                    "generator_url": alert.get("generatorURL"),
                    "receiver": payload.get("receiver"),
                },
            )
        )
    return events
