"""
Recurrence (regression) detector.

Sets `IncidentRecord.regressed`, which the reward function uses as a durable-fix signal:
  - True  : the same alert (services + anomaly type) fired again within `window` of a
            verified recovery, so the fix did not hold.
  - False : the window elapsed with no re-fire.
  - None  : still inside the window, so unknown.

Limits: identity is the alert key, not a causal link. A new, unrelated problem on the
same service and anomaly type inside the window is counted as a regression.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from shared.utils import get_logger

if TYPE_CHECKING:
    from shared.incident_store import IncidentStore
    from shared.schemas import AnomalyEvent, IncidentRecord

logger = get_logger("regression_detector")

DEFAULT_WINDOW = timedelta(hours=1)


def alert_key(anomaly: AnomalyEvent) -> str:
    """Identity of an alert, shared by deduplication and regression detection."""
    return f"{':'.join(anomaly.affected_services)}:{anomaly.anomaly_type.value}"


class RegressionDetector:
    def __init__(self, store: IncidentStore, window: timedelta = DEFAULT_WINDOW) -> None:
        self.store = store
        self.window = window

    def on_new_incident(self, incident: IncidentRecord) -> list[IncidentRecord]:
        """Mark recently recovered incidents with the same alert key as regressed."""
        if incident.anomaly_event is None:
            return []
        key = alert_key(incident.anomaly_event)
        changed: list[IncidentRecord] = []
        for prior in self.store.list_recovered():
            if prior.incident_id == incident.incident_id or prior.regressed is True:
                continue
            if prior.anomaly_event is None or alert_key(prior.anomaly_event) != key:
                continue
            if prior.resolved_at is None or not (
                timedelta(0) <= incident.created_at - prior.resolved_at <= self.window
            ):
                continue
            prior.regressed = True
            self.store.save(prior)
            self.store.record_audit(
                prior.incident_id,
                "regression_detector",
                "regressed",
                {"alert_key": key, "recurred_as": incident.incident_id},
            )
            logger.info("regression_detected", incident_id=prior.incident_id, recurred_as=incident.incident_id)
            changed.append(prior)
        return changed

    def settle(self, now: datetime | None = None) -> list[IncidentRecord]:
        """Mark recovered incidents stable once the window passed with no re-fire."""
        now = now or datetime.now(UTC)
        changed: list[IncidentRecord] = []
        for inc in self.store.list_recovered():
            if inc.regressed is not None or inc.resolved_at is None:
                continue
            if now - inc.resolved_at < self.window:
                continue
            inc.regressed = False
            self.store.save(inc)
            self.store.record_audit(
                inc.incident_id, "regression_detector", "stable", {"window_s": self.window.total_seconds()}
            )
            changed.append(inc)
        return changed
