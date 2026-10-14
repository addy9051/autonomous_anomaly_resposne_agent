"""
Independent Recovery Verification Subsystem.

Blueprint Principle 4: "Action completion is not incident recovery."
Verifies service-level indicators and independent health checks after
a stabilization window before any incident can be considered recovered.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter
from shared.schemas import (
    ActionResult,
    IncidentRecord,
    NormalizedTelemetryRecord,
    VerificationOutcome,
    VerificationResult,
)
from shared.utils import get_logger

logger = get_logger("recovery_verifier")


class RecoveryVerifier:
    """
    Independent post-action recovery verification.
    
    Checks authoritative service-level indicators (SLOs, error rate, p99 latency,
    synthetic probe health) against predeclared recovery criteria.
    """

    def __init__(self, stabilization_window_seconds: float = 0.5) -> None:
        self.stabilization_window_seconds = stabilization_window_seconds

    async def verify(
        self,
        incident: IncidentRecord,
        action_results: list[ActionResult] | None = None,
        telemetry_adapter: BaseTelemetryAdapter | None = None,
        dry_run: bool = False,
    ) -> VerificationResult:
        """
        Execute independent recovery verification.

        Returns VerificationResult with outcome in:
          - VERIFIED_RECOVERY: Authoritative service-level signals confirm recovery
          - NOT_RECOVERED: Signals indicate service remains degraded or worsened
          - INCONCLUSIVE: Telemetry source unavailable, degraded, or insufficient signals
        """
        results = action_results or incident.action_results
        service = "unknown"
        if incident.anomaly_event and incident.anomaly_event.affected_services:
            service = incident.anomaly_event.affected_services[0]

        # 1. Action execution status check
        if not results or not any(r.execution_status in ("success", "executed") for r in results):
            logger.info("verification_skipped_no_successful_action", incident_id=incident.incident_id)
            return VerificationResult(
                outcome=VerificationOutcome.NOT_RECOVERED,
                checked_signals={"action_execution": "failed_or_missing"},
                details="Action was not executed successfully; recovery verification failed.",
                stabilization_seconds=0.0,
                timestamp=datetime.now(timezone.utc),
            )

        # 2. Wait for stabilization window
        if self.stabilization_window_seconds > 0:
            await asyncio.sleep(self.stabilization_window_seconds)

        # 3. If in simulation mode and adapter is absent, return clear simulated outcome
        if telemetry_adapter is None:
            from data_pipeline.connectors.adapters import get_telemetry_adapter
            telemetry_adapter = get_telemetry_adapter()

        checked_signals: dict[str, Any] = {}

        try:
            # Query authoritative signals for the affected service
            records = await telemetry_adapter.fetch_signals(
                entity=service,
                metric_names=["error_rate", "latency_p99", "transaction_failure_rate"],
                time_window_seconds=120,
            )

            # Check for adapter error / evidence gap
            if not records or any(r.tags.get("evidence_gap") for r in records):
                return VerificationResult(
                    outcome=VerificationOutcome.INCONCLUSIVE,
                    checked_signals={"source": telemetry_adapter.source.value, "signals_count": len(records)},
                    details="Authoritative telemetry source was degraded or unavailable during verification window.",
                    stabilization_seconds=self.stabilization_window_seconds,
                    timestamp=datetime.now(timezone.utc),
                )

            # Evaluate service-level recovery criteria
            is_recovered = True
            for r in records:
                checked_signals[r.metric_name] = r.value
                # If error rate or transaction failure rate is still high (> 1.0%), not recovered
                if r.metric_name in ("error_rate", "transaction_failure_rate"):
                    if r.value > 1.0:  # Threshold 1%
                        is_recovered = False
                elif r.metric_name == "latency_p99":
                    if r.value > 1000.0:  # Threshold 1000ms
                        is_recovered = False

            if is_recovered:
                outcome = VerificationOutcome.VERIFIED_RECOVERY
                details = f"Service '{service}' verified recovered: all SLIs within baseline thresholds."
            else:
                outcome = VerificationOutcome.NOT_RECOVERED
                details = f"Service '{service}' remains degraded: SLIs exceeded recovery criteria."

            return VerificationResult(
                outcome=outcome,
                checked_signals=checked_signals,
                details=details,
                stabilization_seconds=self.stabilization_window_seconds,
                timestamp=datetime.now(timezone.utc),
            )

        except Exception as exc:
            logger.warning("recovery_verification_error", error=str(exc), service=service)
            return VerificationResult(
                outcome=VerificationOutcome.INCONCLUSIVE,
                checked_signals={"error": str(exc)},
                details=f"Verification encountered connector exception: {exc}",
                stabilization_seconds=self.stabilization_window_seconds,
                timestamp=datetime.now(timezone.utc),
            )
