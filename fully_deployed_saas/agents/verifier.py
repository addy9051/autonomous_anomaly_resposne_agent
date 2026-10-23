"""
Closed-Loop Post-Remediation Recovery Verifier.
Independently re-evaluates telemetry after action execution to guarantee recovery.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from connectors.adapters import get_tenant_telemetry_adapter
from core.schemas import VerifierOutcome
from core.utils import get_logger
from database.connection import execute

logger = get_logger("recovery_verifier")


class ClosedLoopVerifier:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id

    async def verify_recovery(
        self,
        incident_id: str,
        service: str,
        wait_seconds: float = 2.0,
    ) -> VerifierOutcome:
        """
        Poll telemetry signals post-action to verify that anomaly is resolved.
        Fails safe to INCONCLUSIVE if signals cannot be verified.
        """
        logger.info("verification_started", incident_id=incident_id, service=service)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        adapter = await get_tenant_telemetry_adapter(self.tenant_id)
        try:
            records = await adapter.fetch_evidence(service=service, lookback_minutes=5)
            anomalous = [r for r in records if r.is_anomaly]

            if not records:
                outcome = VerifierOutcome.INCONCLUSIVE
            elif not anomalous:
                outcome = VerifierOutcome.RECOVERED
            else:
                outcome = VerifierOutcome.NOT_RECOVERED
        except Exception as exc:
            logger.error("verification_error", error=str(exc))
            outcome = VerifierOutcome.INCONCLUSIVE

        # Update action_proposals in PostgreSQL
        await execute(
            """
            UPDATE action_proposals
            SET verifier_outcome = $1, updated_at = NOW()
            WHERE tenant_id = $2 AND incident_id = $3
            """,
            outcome.value,
            self.tenant_id,
            incident_id,
        )

        logger.info("verification_completed", incident_id=incident_id, outcome=outcome.value)
        return outcome
