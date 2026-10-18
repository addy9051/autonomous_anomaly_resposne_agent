"""Tests for dashboard performance, audit, and autonomy-control APIs."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

import api
from shared.incident_store import IncidentStore
from shared.schemas import IncidentRecord, IncidentStatus

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, IncidentStore]:
    store = IncidentStore(tmp_path / "incidents.db")
    monkeypatch.setattr(api, "orchestrator", SimpleNamespace(store=store))
    client = TestClient(api.app)
    return client, store


def _identity_headers() -> dict[str, str]:
    return {api.get_settings().app.trusted_identity_header: "dashboard-test"}


def test_performance_metrics_are_derived_from_windowed_incidents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, store = _client(monkeypatch, tmp_path)
    store.save(
        IncidentRecord(
            incident_id="recovered",
            status=IncidentStatus.VERIFIED_RECOVERED,
            auto_resolved=True,
            time_to_detect_seconds=12,
            time_to_mitigate_seconds=72,
            total_llm_cost_usd=0.02,
            total_llm_tokens_used=200,
        )
    )
    store.save(
        IncidentRecord(
            incident_id="override",
            status=IncidentStatus.ESCALATED,
            human_overrode=True,
            false_positive=True,
            time_to_detect_seconds=24,
            total_llm_cost_usd=0.04,
            total_llm_tokens_used=400,
        )
    )

    response = client.get("/api/v1/metrics/performance?window=7d", headers=_identity_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["totals"] == {
        "incidents": 2,
        "recovered": 1,
        "auto_resolved": 1,
        "human_overrides": 1,
        "false_positives": 1,
    }
    assert body["rates"]["resolution"] == 0.5
    assert body["timings_seconds"]["mean_time_to_detect"] == 18
    assert body["cost"]["per_incident_usd"] == 0.03
    assert body["cost"]["tokens_per_incident"] == 300


def test_audit_endpoint_supports_incident_filter_and_cursor(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, store = _client(monkeypatch, tmp_path)
    store.record_audit("incident-a", "sre", "approved", {"note": "first"})
    store.record_audit("incident-b", "agent", "detected", {"note": "second"})
    latest = client.get("/api/v1/audit?limit=1", headers=_identity_headers()).json()["entries"][0]

    response = client.get(
        f"/api/v1/audit?incident_id=incident-a&before_id={latest['id']}",
        headers=_identity_headers(),
    )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["entries"][0]["incident_id"] == "incident-a"
    assert response.json()["entries"][0]["details"] == {"note": "first"}


def test_agent_controls_are_persisted_audited_and_validated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, store = _client(monkeypatch, tmp_path)
    headers = _identity_headers()

    pause = client.put("/api/v1/agent/controls", json={"paused": True}, headers=headers)
    autonomy = client.put(
        "/api/v1/agent/controls",
        json={"service": "checkout-api", "autonomy": "approve_to_act"},
        headers=headers,
    )

    assert pause.status_code == autonomy.status_code == 200
    assert client.get("/api/v1/agent/controls", headers=headers).json() == {
        "paused": True,
        "service_autonomy": {"checkout-api": "approve_to_act"},
    }
    assert store.list_audit(limit=10)[0]["action"] == "agent_controls_updated"
    assert (
        client.put(
            "/api/v1/agent/controls",
            json={"service": "checkout-api"},
            headers=headers,
        ).status_code
        == 422
    )

    restarted_store = IncidentStore(tmp_path / "incidents.db")
    assert restarted_store.get_agent_controls()["paused"] is True


def test_incident_feedback_is_stored_and_audited(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, store = _client(monkeypatch, tmp_path)
    api.orchestrator.active_incidents = {}
    store.save(IncidentRecord(incident_id="inc-1", status=IncidentStatus.VERIFIED_RECOVERED))
    headers = _identity_headers()

    missing_category = client.post("/api/v1/incidents/inc-1/feedback", json={"verdict": "unhelpful"}, headers=headers)
    ok = client.post(
        "/api/v1/incidents/inc-1/feedback",
        json={"verdict": "unhelpful", "category": "wrong_root_cause", "comment": "It was DNS"},
        headers=headers,
    )
    unknown = client.post("/api/v1/incidents/nope/feedback", json={"verdict": "helpful"}, headers=headers)

    assert missing_category.status_code == 422
    assert ok.status_code == 200
    assert unknown.status_code == 404
    assert "wrong_root_cause" in store.get("inc-1").human_feedback
    assert store.list_audit(limit=1, incident_id="inc-1")[0]["action"] == "feedback_submitted"
