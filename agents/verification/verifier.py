"""
Independent Recovery Verification Subsystem.

Blueprint Principle 4: "Action completion is not incident recovery."
Verifies service-level indicators and independent health checks after
a stabilization window before any incident can be considered recovered.

Security hardening:
- Rejects simulator-tagged evidence in non-dry-run verification
- Requires minimum signal coverage before declaring recovery
- Validates evidence freshness against the verification window
- Accepts configurable thresholds from policy evaluation criteria
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from shared.schemas import (
    ActionResult,
    IncidentRecord,
    NormalizedTelemetryRecord,
    TelemetryConnectorSource,
    VerificationOutcome,
    VerificationResult,
)
from shared.utils import get_logger

logger = get_logger("recovery_verifier")

if TYPE_CHECKING:
    from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter

if TYPE_CHECKING:
    from data_pipeline.connectors.adapters.base import BaseTelemetryAdapter

# Minimum set of signals required to declare recovery.
# If any of these are missing, verification is INCONCLUSIVE.
REQUIRED_SIGNALS = {"error_rate", "latency_p99"}

# Conservative default thresholds (used when policy doesn't provide criteria)
DEFAULT_ERROR_RATE_THRESHOLD = 1.0  # 1%
DEFAULT_LATENCY_P99_THRESHOLD_MS = 1000.0  # 1000ms
DEFAULT_TXN_FAILURE_RATE_THRESHOLD = 1.0  # 1%


class RecoveryVerifier:
    """
    Independent post-action recovery verification.

    Checks authoritative service-level indicators (SLOs, error rate, p99 latency,
    synthetic probe health) against predeclared recovery criteria. It fails closed
    with INCONCLUSIVE when evidence is insufficient, simulated, stale, or missing
    required signals.
    """

    def __init__(
        self,
        stabilization_window_seconds: float = 30.0,
        stabilization_window_seconds_dry_run: float = 0.1,
    ) -> None:
        self.stabilization_window_seconds = stabilization_window_seconds
        self.stabilization_window_seconds_dry_run = stabilization_window_seconds_dry_run

    async def verify(
        self,
        incident: IncidentRecord,
        action_results: list[ActionResult] | None = None,
        telemetry_adapter: BaseTelemetryAdapter | None = None,
        dry_run: bool = False,
        verification_criteria: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """
        Execute independent recovery verification.

        Returns VerificationResult with outcome in:
          - VERIFIED_RECOVERY: Authoritative service-level signals confirm recovery
          - NOT_RECOVERED: Signals indicate service remains degraded or worsened
          - INCONCLUSIVE: Telemetry source unavailable, degraded, or insufficient signals
        """
        results = action_results or incident.action_results
        criteria = verification_criteria or {}
        stabilization_window_seconds = (
            self.stabilization_window_seconds_dry_run if dry_run else self.stabilization_window_seconds
        )
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
                timestamp=datetime.now(UTC),
            )

        # 2. Wait for stabilization window
        if stabilization_window_seconds > 0:
            await asyncio.sleep(stabilization_window_seconds)

        # 3. Acquire telemetry adapter
        if telemetry_adapter is None:
            from data_pipeline.connectors.adapters import get_telemetry_adapter

            telemetry_adapter = get_telemetry_adapter()

        checked_signals: dict[str, Any] = {}
        time_window_seconds = 120

        try:
            # Query authoritative signals for the affected service
            records = await telemetry_adapter.fetch_signals(
                entity=service,
                metric_names=["error_rate", "latency_p99", "transaction_failure_rate"],
                time_window_seconds=time_window_seconds,
            )

            # 4. Filter out simulator / mock evidence when executing against real infrastructure
            if not dry_run:
                authoritative_records = self._filter_authoritative_records(records)
                if len(authoritative_records) < len(records):
                    filtered_count = len(records) - len(authoritative_records)
                    logger.info(
                        "verification_filtered_simulated_evidence",
                        filtered=filtered_count,
                        remaining=len(authoritative_records),
                        incident_id=incident.incident_id,
                    )
                records = authoritative_records

            # 5. Check for adapter error / evidence gap
            if not records or any(r.tags.get("evidence_gap") for r in records):
                return VerificationResult(
                    outcome=VerificationOutcome.INCONCLUSIVE,
                    checked_signals={"source": telemetry_adapter.source.value, "signals_count": len(records)},
                    details="Authoritative telemetry source was degraded or unavailable during verification window.",
                    stabilization_seconds=stabilization_window_seconds,
                    timestamp=datetime.now(UTC),
                )

            # 6. Freshness check — reject stale evidence
            now = datetime.now(UTC)
            max_age_seconds = 2.0 * time_window_seconds
            fresh_records = [r for r in records if (now - r.timestamp).total_seconds() <= max_age_seconds]
            if not fresh_records:
                return VerificationResult(
                    outcome=VerificationOutcome.INCONCLUSIVE,
                    checked_signals={
                        "source": telemetry_adapter.source.value,
                        "total_records": len(records),
                        "fresh_records": 0,
                        "max_age_seconds": max_age_seconds,
                    },
                    details=(
                        f"All {len(records)} telemetry records are older than {max_age_seconds}s; evidence is stale."
                    ),
                    stabilization_seconds=stabilization_window_seconds,
                    timestamp=datetime.now(UTC),
                )
            records = fresh_records

            # 7. Minimum signal coverage check
            available_signals = {r.metric_name for r in records}
            missing_signals = REQUIRED_SIGNALS - available_signals
            if missing_signals:
                return VerificationResult(
                    outcome=VerificationOutcome.INCONCLUSIVE,
                    checked_signals={
                        "source": telemetry_adapter.source.value,
                        "available": sorted(available_signals),
                        "missing_required": sorted(missing_signals),
                    },
                    details=(
                        f"Required verification signals missing: {sorted(missing_signals)}. Cannot confirm recovery."
                    ),
                    stabilization_seconds=stabilization_window_seconds,
                    timestamp=datetime.now(UTC),
                )

            # 8. Evaluate service-level recovery criteria (use policy thresholds or defaults)
            error_rate_threshold = criteria.get("error_rate_threshold", DEFAULT_ERROR_RATE_THRESHOLD)
            latency_threshold = criteria.get("latency_threshold_ms", DEFAULT_LATENCY_P99_THRESHOLD_MS)
            txn_failure_threshold = criteria.get("txn_failure_rate_threshold", DEFAULT_TXN_FAILURE_RATE_THRESHOLD)

            is_recovered = True
            for r in records:
                checked_signals[r.metric_name] = r.value
                if r.metric_name in ("error_rate", "transaction_failure_rate"):
                    threshold = (
                        txn_failure_threshold if r.metric_name == "transaction_failure_rate" else error_rate_threshold
                    )
                    if r.value > threshold:
                        is_recovered = False
                elif r.metric_name == "latency_p99":
                    if r.value > latency_threshold:
                        is_recovered = False

            checked_signals["_thresholds"] = {
                "error_rate": error_rate_threshold,
                "latency_p99_ms": latency_threshold,
                "txn_failure_rate": txn_failure_threshold,
            }

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
                stabilization_seconds=stabilization_window_seconds,
                timestamp=datetime.now(UTC),
            )

        except Exception as exc:
            logger.warning("recovery_verification_error", error=str(exc), service=service)
            return VerificationResult(
                outcome=VerificationOutcome.INCONCLUSIVE,
                checked_signals={"error": str(exc)},
                details=f"Verification encountered connector exception: {exc}",
                stabilization_seconds=stabilization_window_seconds,
                timestamp=datetime.now(UTC),
            )

    @staticmethod
    def _filter_authoritative_records(
        records: list[NormalizedTelemetryRecord],
    ) -> list[NormalizedTelemetryRecord]:
        """
        Filter out simulator-tagged or mock evidence.

        In non-dry-run verification, only authoritative (real) telemetry records
        should be used to determine recovery. Simulator records are explicitly
        excluded to prevent false recovery declarations.
        """
        authoritative: list[NormalizedTelemetryRecord] = []
        for r in records:
            # Reject records from the simulator adapter
            if r.connector_source == TelemetryConnectorSource.SIMULATOR or r.is_simulated:
                continue
            # Reject records explicitly tagged as simulated or mock
            if (
                r.tags.get("simulated")
                or r.tags.get("is_simulated")
                or r.tags.get("is_mock")
                or r.tags.get("simulated_fallback")
                or r.tags.get("simulated_mock")
            ):
                continue
            authoritative.append(r)
        return authoritative
