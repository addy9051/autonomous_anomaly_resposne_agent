"""Recurrence detector: sets IncidentRecord.regressed from re-fires of the same alert."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents.feedback.regression import RegressionDetector, alert_key
from shared.incident_store import IncidentStore
from shared.schemas import (
    AnomalyEvent,
    AnomalyType,
    IncidentRecord,
    IncidentStatus,
    MetricsSnapshot,
    Severity,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
WINDOW = timedelta(hours=1)


def _anomaly(service: str = "checkout", kind: AnomalyType = AnomalyType.LATENCY_SPIKE) -> AnomalyEvent:
    return AnomalyEvent(
        severity=Severity.HIGH,
        affected_services=[service],
        anomaly_type=kind,
        metrics_snapshot=MetricsSnapshot(),
        reasoning="test",
        confidence=0.9,
    )


def _incident(
    resolved_ago: timedelta | None,
    *,
    service: str = "checkout",
    kind: AnomalyType = AnomalyType.LATENCY_SPIKE,
    status: IncidentStatus = IncidentStatus.VERIFIED_RECOVERED,
) -> IncidentRecord:
    inc = IncidentRecord(status=status, anomaly_event=_anomaly(service, kind), created_at=NOW - timedelta(hours=5))
    if resolved_ago is not None:
        inc.resolved_at = NOW - resolved_ago
    return inc


@pytest.fixture
def store(tmp_path) -> IncidentStore:  # noqa: ANN001
    return IncidentStore(tmp_path / "incidents.db")


@pytest.fixture
def detector(store: IncidentStore) -> RegressionDetector:
    return RegressionDetector(store, WINDOW)


def _refire(created_at: datetime = NOW, **kw) -> IncidentRecord:  # noqa: ANN003
    inc = IncidentRecord(status=IncidentStatus.DETECTED, anomaly_event=_anomaly(**kw))
    inc.created_at = created_at
    return inc


def test_refire_inside_window_marks_regressed(store: IncidentStore, detector: RegressionDetector) -> None:
    prior = _incident(timedelta(minutes=20))
    store.save(prior)
    changed = detector.on_new_incident(_refire())
    assert [c.incident_id for c in changed] == [prior.incident_id]
    assert store.get(prior.incident_id).regressed is True  # type: ignore[union-attr]


def test_refire_outside_window_is_not_a_regression(store: IncidentStore, detector: RegressionDetector) -> None:
    prior = _incident(timedelta(hours=3))
    store.save(prior)
    assert detector.on_new_incident(_refire()) == []
    assert store.get(prior.incident_id).regressed is None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("service", "kind"),
    [("payments", AnomalyType.LATENCY_SPIKE), ("checkout", AnomalyType.ERROR_RATE)],
)
def test_different_service_or_type_is_unrelated(
    store: IncidentStore, detector: RegressionDetector, service: str, kind: AnomalyType
) -> None:
    store.save(_incident(timedelta(minutes=5)))
    assert detector.on_new_incident(_refire(service=service, kind=kind)) == []


def test_unrecovered_incident_is_never_marked(store: IncidentStore, detector: RegressionDetector) -> None:
    store.save(_incident(timedelta(minutes=5), status=IncidentStatus.ESCALATED))
    assert detector.on_new_incident(_refire()) == []


def test_missing_resolved_at_is_skipped(store: IncidentStore, detector: RegressionDetector) -> None:
    store.save(_incident(None))
    assert detector.on_new_incident(_refire()) == []


def test_incident_is_not_its_own_regression(store: IncidentStore, detector: RegressionDetector) -> None:
    prior = _incident(timedelta(minutes=1))
    store.save(prior)
    assert detector.on_new_incident(prior) == []


def test_settle_marks_stable_only_after_window(store: IncidentStore, detector: RegressionDetector) -> None:
    old, fresh = _incident(timedelta(hours=2)), _incident(timedelta(minutes=10))
    store.save(old)
    store.save(fresh)
    assert [c.incident_id for c in detector.settle(NOW)] == [old.incident_id]
    assert store.get(old.incident_id).regressed is False  # type: ignore[union-attr]
    assert store.get(fresh.incident_id).regressed is None  # type: ignore[union-attr]


def test_settle_never_overrides_a_regression(store: IncidentStore, detector: RegressionDetector) -> None:
    prior = _incident(timedelta(hours=2))
    prior.regressed = True
    store.save(prior)
    assert detector.settle(NOW) == []
    assert store.get(prior.incident_id).regressed is True  # type: ignore[union-attr]


def test_regression_is_audited(store: IncidentStore, detector: RegressionDetector) -> None:
    store.save(_incident(timedelta(minutes=5)))
    detector.on_new_incident(_refire())
    with store._get_connection() as conn:
        actions = [r["action"] for r in conn.execute("SELECT action FROM audit_trail")]
    assert actions == ["regressed"]


def test_alert_key_matches_dedup_format() -> None:
    assert alert_key(_anomaly("a")) == "a:latency_spike"
