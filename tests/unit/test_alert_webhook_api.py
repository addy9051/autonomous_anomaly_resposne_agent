"""Endpoint test for the Alertmanager webhook."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

import api

if TYPE_CHECKING:
    import pytest


def test_webhook_starts_investigation_per_firing_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    incident = MagicMock(incident_id="INC-1")
    fake.process_anomaly = AsyncMock(return_value=incident)
    monkeypatch.setattr(api, "orchestrator", fake)

    payload = {
        "receiver": "sre",
        "alerts": [
            {"status": "firing", "labels": {"alertname": "HighErrorRate", "service": "checkout-api"}},
            {"status": "resolved", "labels": {"alertname": "HighErrorRate", "service": "checkout-api"}},
        ],
    }
    client = TestClient(api.app)
    resp = client.post(
        "/api/v1/alerts/alertmanager",
        json=payload,
        headers={api.get_settings().app.trusted_identity_header: "alertmanager"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["alerts_received"] == 2
    assert body["investigations_started"] == 1
    assert body["incident_ids"] == ["INC-1"]
    assert fake.process_anomaly.await_args.kwargs["dry_run"] is True
