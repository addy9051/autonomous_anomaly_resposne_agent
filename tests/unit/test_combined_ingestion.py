"""Tests for combined multi-vendor ingestion: composite adapter, detector, service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from data_pipeline.connectors.adapters.base import TelemetrySourceError
from data_pipeline.connectors.adapters.composite import CompositeTelemetryAdapter
from data_pipeline.connectors.ingestion import StreamDetector, TelemetryIngestionService
from data_pipeline.connectors.query_templates import resolve_query
from shared.schemas import NormalizedTelemetryRecord, TelemetryConnectorSource


def _rec(value: float, src=TelemetryConnectorSource.PROMETHEUS, offset: int = 0, metric: str = "error_rate"):
    return NormalizedTelemetryRecord(
        timestamp=datetime.now(timezone.utc) + timedelta(seconds=offset),
        entity="svc",
        connector_source=src,
        metric_name=metric,
        value=value,
    )


def _adapter(src, records=None, error=None):
    a = MagicMock()
    a.source = src
    if error:
        a.fetch_signals = AsyncMock(side_effect=TelemetrySourceError(error))
    else:
        a.fetch_signals = AsyncMock(return_value=records or [])
    return a


@pytest.mark.asyncio
async def test_composite_merges_and_tolerates_partial_failure() -> None:
    ok = _adapter(TelemetryConnectorSource.PROMETHEUS, [_rec(1.0)])
    bad = _adapter(TelemetryConnectorSource.DATADOG, error="dd down")
    comp = CompositeTelemetryAdapter([ok, bad])
    records = await comp.fetch_signals("svc", ["error_rate"], 60)
    assert len(records) == 1
    assert "datadog" in comp.last_failures


@pytest.mark.asyncio
async def test_composite_raises_when_all_fail() -> None:
    comp = CompositeTelemetryAdapter(
        [_adapter(TelemetryConnectorSource.PROMETHEUS, error="a"), _adapter(TelemetryConnectorSource.SPLUNK, error="b")]
    )
    with pytest.raises(TelemetrySourceError):
        await comp.fetch_signals("svc", ["error_rate"], 60)


def test_detector_flags_spike_and_corroboration() -> None:
    det = StreamDetector(z_threshold=3.0, min_samples=5)
    for i in range(10):
        assert det.observe(_rec(1.0 + (i % 2) * 0.1, offset=i)) is None
    hit = det.observe(_rec(50.0, offset=20))
    assert hit is not None and hit["flagged_by"] == ["prometheus"]
    for i in range(10):
        det.observe(_rec(1.0, src=TelemetryConnectorSource.DATADOG, offset=i))
    hit2 = det.observe(_rec(50.0, src=TelemetryConnectorSource.DATADOG, offset=20))
    assert hit2 is not None and hit2["flagged_by"] == ["datadog", "prometheus"]


def test_detector_ignores_replayed_samples() -> None:
    det = StreamDetector(z_threshold=3.0, min_samples=2)
    r = _rec(1.0)
    det.observe(r)
    assert det.observe(r) is None
    assert len(det._series[("prometheus", "svc", "error_rate")].values) == 1


@pytest.mark.asyncio
async def test_service_isolates_vendor_failure_and_escalates_once() -> None:
    orch = MagicMock()
    orch.telemetry_history = []
    orch.process_event = AsyncMock(return_value=None)
    good = _adapter(TelemetryConnectorSource.PROMETHEUS, [_rec(1.0)])
    bad = _adapter(TelemetryConnectorSource.DATADOG, error="down")
    svc = TelemetryIngestionService(
        orch, {"prometheus": good, "datadog": bad}, ["svc"], z_threshold=3.0, min_samples=3, cooldown_seconds=999
    )
    assert await svc.poll_once("prometheus", good) == 1
    with pytest.raises(TelemetrySourceError):
        await svc.poll_once("datadog", bad)

    for i in range(6):
        svc.handle_record(_rec(1.0, offset=i))
    assert svc.handle_record(_rec(90.0, offset=30)) is True
    assert svc.handle_record(_rec(95.0, offset=31)) is False  # cooldown
    import asyncio

    await asyncio.sleep(0)
    await asyncio.sleep(0)
    orch.process_event.assert_awaited_once()


def test_query_templates() -> None:
    q = resolve_query("datadog", "latency_p99", "checkout")
    assert q is not None and "checkout" in q and "{entity}" not in q
    assert resolve_query("prometheus", "unknown_metric", "x") is None
