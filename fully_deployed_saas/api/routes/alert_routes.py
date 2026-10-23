"""
Alert Ingestion Webhooks (Universal Webhook & Prometheus Alertmanager).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, status
from pydantic import BaseModel

from agents.orchestrator import IncidentOrchestrator
from core.auth import TenantDep
from core.schemas import AnomalyAlert, SREBrief, Severity
from core.utils import get_logger

logger = get_logger("alert_routes")
router = APIRouter(prefix="/alerts", tags=["Alert Ingestion"])


@router.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def ingest_universal_alert(
    alert: AnomalyAlert,
    tenant: TenantDep,
) -> dict[str, Any]:
    """
    Universal Alert Intake Endpoint.
    Accepts alerts from Datadog, Grafana, CloudWatch, or custom scripts.
    """
    orchestrator = IncidentOrchestrator(tenant.tenant_id)
    record, brief = await orchestrator.process_alert(alert)

    return {
        "status": "processed",
        "incident_id": record.incident_id,
        "incident_status": record.status.value,
        "sre_brief": brief.model_dump(),
    }


@router.post("/alertmanager", status_code=status.HTTP_202_ACCEPTED)
async def ingest_alertmanager(
    payload: dict[str, Any],
    tenant: TenantDep,
) -> dict[str, Any]:
    """
    Native Prometheus Alertmanager Webhook Intake.
    Automatically unpacks Alertmanager alerts list and runs response orchestrator.
    """
    alerts = payload.get("alerts", [])
    if not alerts:
        return {"status": "ignored", "reason": "empty alerts list"}

    first_alert = alerts[0]
    labels = first_alert.get("labels", {})
    annotations = first_alert.get("annotations", {})

    severity_str = labels.get("severity", "medium").lower()
    try:
        severity = Severity(severity_str)
    except ValueError:
        severity = Severity.MEDIUM

    alert_obj = AnomalyAlert(
        title=annotations.get("summary") or labels.get("alertname", "Prometheus Alert"),
        service=labels.get("service") or labels.get("job", "unknown-service"),
        anomaly_type=labels.get("alertname", "PrometheusAnomaly"),
        severity=severity,
        summary=annotations.get("description", ""),
        labels=labels,
        source="alertmanager",
    )

    orchestrator = IncidentOrchestrator(tenant.tenant_id)
    record, brief = await orchestrator.process_alert(alert_obj)

    return {
        "status": "processed",
        "incident_id": record.incident_id,
        "incident_status": record.status.value,
        "sre_brief": brief.model_dump(),
    }
