"""Tests for provenance fields and the deploy-events source."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agents.diagnosis.evidence import collect_deploy_evidence, record_to_evidence
from data_pipeline.connectors import deploy_events
from data_pipeline.connectors.adapters.base import TelemetrySourceError
from shared.config import get_settings
from shared.schemas import NormalizedTelemetryRecord, TelemetryConnectorSource

if TYPE_CHECKING:
    from collections.abc import Iterator

    from shared.config import IntegrationConfig


def test_simulator_source_marks_simulated() -> None:
    r = NormalizedTelemetryRecord(entity="s", metric_name="m", value=1.0)
    assert r.is_simulated is True


def test_legacy_tag_marks_simulated() -> None:
    r = NormalizedTelemetryRecord(
        entity="s",
        metric_name="m",
        value=1.0,
        connector_source=TelemetryConnectorSource.PROMETHEUS,
        tags={"is_mock": True},
    )
    assert r.is_simulated is True


def test_real_record_not_simulated_and_has_freshness() -> None:
    old = datetime.now(UTC) - timedelta(seconds=90)
    r = NormalizedTelemetryRecord(
        entity="s",
        metric_name="m",
        value=1.0,
        timestamp=old,
        connector_source=TelemetryConnectorSource.PROMETHEUS,
    )
    assert r.is_simulated is False
    assert 85 <= r.freshness_seconds <= 120
    ev = record_to_evidence(r)
    assert ev["simulated"] is False and ev["freshness_seconds"] == r.freshness_seconds
    assert ev["ingested_at"]


def _resp(status: int, body: object) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    return resp


@pytest.fixture
def configured(monkeypatch: pytest.MonkeyPatch) -> Iterator[IntegrationConfig]:
    cfg = get_settings().integrations
    monkeypatch.setattr(cfg, "github_deploy_repo", "acme/payments")
    monkeypatch.setattr(cfg, "github_token", "t")
    return cfg


@pytest.mark.asyncio
async def test_deployments_filtered_to_window(configured: IntegrationConfig) -> None:
    now = datetime.now(UTC)
    items = [
        {
            "created_at": (now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sha": "abcdef123456",
            "ref": "main",
            "environment": "production",
            "creator": {"login": "dev"},
            "url": "http://x/1",
        },
        {"created_at": (now - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"), "sha": "old", "ref": "main"},
    ]
    client = MagicMock()
    client.get = AsyncMock(return_value=_resp(200, items))
    recs = await deploy_events.fetch_recent_deployments(3600, client=client)
    assert len(recs) == 1
    assert recs[0].tags["sha"] == "abcdef123456"
    assert recs[0].source_ref == "http://x/1"
    assert recs[0].is_simulated is False


@pytest.mark.asyncio
async def test_deployments_http_error_raises(configured: IntegrationConfig) -> None:
    client = MagicMock()
    client.get = AsyncMock(return_value=_resp(403, {}))
    with pytest.raises(TelemetrySourceError):
        await deploy_events.fetch_recent_deployments(3600, client=client)
    client.get = AsyncMock(side_effect=httpx.ConnectError("x"))
    with pytest.raises(TelemetrySourceError):
        await deploy_events.fetch_recent_deployments(3600, client=client)


@pytest.mark.asyncio
async def test_unconfigured_is_noop() -> None:
    with patch.object(deploy_events, "is_configured", return_value=False):
        assert await collect_deploy_evidence() == ([], [])


@pytest.mark.asyncio
async def test_deploy_failure_becomes_gap() -> None:
    with (
        patch.object(deploy_events, "is_configured", return_value=True),
        patch.object(deploy_events, "fetch_recent_deployments", AsyncMock(side_effect=TelemetrySourceError("x"))),
    ):
        ev, gaps = await collect_deploy_evidence()
    assert ev == [] and gaps == ["github:deployments"]
