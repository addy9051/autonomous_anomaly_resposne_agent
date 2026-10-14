"""Tests for Alertmanager-triggered investigation intake."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from data_pipeline.connectors.alertmanager_intake import parse_alertmanager_payload
from orchestrator import AgentOrchestrator
from shared.schemas import AnomalyType, Severity
from shared.utils import LLMCostTracker


def _alert(status: str = "firing", **labels: str) -> dict[str, Any]:
    base = {"alertname": "HighP99Latency", "severity": "critical", "service": "checkout-api"}
    base.update(labels)
    return {
        "status": status,
        "labels": base,
        "annotations": {"summary": "p99 latency above 2s"},
        "fingerprint": "abc123",
        "startsAt": "2026-01-01T00:00:00Z",
        "generatorURL": "http://prom/graph",
    }


def test_firing_alert_becomes_scoped_anomaly_with_provenance() -> None:
    events = parse_alertmanager_payload({"receiver": "sre", "alerts": [_alert()]})
    assert len(events) == 1
    ev = events[0]
    assert ev.affected_services == ["checkout-api"]
    assert ev.severity == Severity.CRITICAL
    assert ev.anomaly_type == AnomalyType.LATENCY_SPIKE
    assert ev.raw_event["intake_source"] == "alertmanager"
    assert ev.raw_event["fingerprint"] == "abc123"
    assert ev.raw_event["generator_url"] == "http://prom/graph"


def test_resolved_and_unscoped_alerts_are_skipped() -> None:
    unscoped = _alert()
    unscoped["labels"] = {"alertname": "Mystery", "severity": "critical"}
    payload = {"alerts": [_alert(status="resolved"), unscoped]}
    assert parse_alertmanager_payload(payload) == []


def test_unknown_severity_defaults_to_medium() -> None:
    events = parse_alertmanager_payload({"alerts": [_alert(severity="weird")]})
    assert events[0].severity == Severity.MEDIUM


@pytest.mark.asyncio
async def test_process_anomaly_skips_monitoring_agent() -> None:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch.monitoring_agent = MagicMock()
    orch.monitoring_agent.process_event = AsyncMock()
    orch.alert_dedup = MagicMock()
    orch.alert_dedup.should_fire.return_value = False
    orch.alert_dedup.get_suppressed_count.return_value = 1

    anomaly = parse_alertmanager_payload({"alerts": [_alert()]})[0]
    result = await orch.process_anomaly(anomaly, LLMCostTracker(incident_id="pending"), dry_run=True)

    assert result is None  # deduplicated
    orch.monitoring_agent.process_event.assert_not_called()
    orch.alert_dedup.should_fire.assert_called_once()
