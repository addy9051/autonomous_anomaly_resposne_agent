"""Tests for scoped evidence collection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.diagnosis.evidence import collect_evidence
from data_pipeline.connectors.adapters.base import TelemetrySourceError
from shared.schemas import (
    NormalizedTelemetryRecord,
    Severity,
    TelemetryConnectorSource,
    TelemetrySignalType,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def _rec(metric: str, ts: int, tags: dict | None = None) -> NormalizedTelemetryRecord:
    return NormalizedTelemetryRecord(
        timestamp=datetime.fromtimestamp(ts, tz=UTC),
        entity="svc",
        connector_source=TelemetryConnectorSource.PROMETHEUS,
        signal_type=TelemetrySignalType.METRIC,
        metric_name=metric,
        value=1.0,
        unit="value",
        severity=Severity.LOW,
        tags=tags or {},
        raw_query=f'{metric}{{service="svc"}}',
    )


def _adapter(
    side_effect: Callable[..., list[NormalizedTelemetryRecord]] | TelemetrySourceError,
) -> MagicMock:
    adapter = MagicMock()
    adapter.source = TelemetryConnectorSource.PROMETHEUS
    adapter.fetch_signals = AsyncMock(side_effect=side_effect)
    return adapter


@pytest.mark.asyncio
async def test_timeline_sorted_and_missing_metric_flagged() -> None:
    adapter = _adapter(lambda **_: [_rec("error_rate", 200), _rec("latency_p99", 100)])
    evidence, gaps = await collect_evidence(adapter, ["svc"], ["latency_p99", "error_rate", "cpu"])
    assert [e["observed_at"] for e in evidence] == sorted(e["observed_at"] for e in evidence)
    assert gaps == ["prometheus:svc:cpu"]


@pytest.mark.asyncio
async def test_source_failure_is_gap_not_exception() -> None:
    adapter = _adapter(TelemetrySourceError("down"))
    evidence, gaps = await collect_evidence(adapter, ["svc"])
    assert evidence == []
    assert gaps == ["prometheus:svc:*"]


@pytest.mark.asyncio
async def test_one_service_failing_keeps_others() -> None:
    async def fetch(entity: str, **_: object) -> list[NormalizedTelemetryRecord]:
        if entity == "bad":
            raise TelemetrySourceError("x")
        return [_rec("latency_p99", 1)]

    adapter = _adapter(fetch)
    evidence, gaps = await collect_evidence(adapter, ["bad", "good"], ["latency_p99"])
    assert len(evidence) == 1
    assert gaps == ["prometheus:bad:*"]


@pytest.mark.asyncio
async def test_simulated_flag_propagates() -> None:
    adapter = _adapter(lambda **_: [_rec("latency_p99", 1, {"simulated": True})])
    evidence, _ = await collect_evidence(adapter, ["svc"], ["latency_p99"])
    assert evidence[0]["simulated"] is True
    assert "[SIMULATED]" in evidence[0]["description"]
