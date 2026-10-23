"""
Master Multi-Tenant Incident Orchestrator.
Coordinates: Intake → Episodic Memory → Evidence → RAG Diagnosis → Policy Gate → Verification → SRE Brief.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from agents.diagnosis import DiagnosisEngine
from agents.policy import DeterministicPolicyGate
from agents.verifier import ClosedLoopVerifier
from connectors.adapters import get_tenant_telemetry_adapter
from core.schemas import (
    ActionProposal,
    ActionTier,
    AnomalyAlert,
    EvidenceRecord,
    Hypothesis,
    IncidentRecord,
    IncidentStatus,
    SREBrief,
    Severity,
    VerifierOutcome,
)
from core.utils import get_logger
from database.connection import execute, fetch_one

logger = get_logger("orchestrator")


class IncidentOrchestrator:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.diagnosis_engine = DiagnosisEngine(tenant_id)
        self.policy_gate = DeterministicPolicyGate(tenant_id)
        self.verifier = ClosedLoopVerifier(tenant_id)

    async def process_alert(self, alert: AnomalyAlert) -> tuple[IncidentRecord, SREBrief]:
        """Process an inbound alert through the complete governed SRE loop."""
        incident_id = f"inc-{uuid4().hex[:10]}"
        now_iso = datetime.now(UTC).isoformat()
        fingerprint = f"{alert.service}:{alert.anomaly_type}:{alert.severity.value}"

        timeline = [
            {"time": now_iso, "stage": "intake", "message": f"Alert received: {alert.title}"}
        ]

        # Ensure incident record exists in Postgres before generating action proposals
        await execute(
            """
            INSERT INTO incidents (incident_id, tenant_id, title, status, severity, service, fingerprint, timeline, created_at, updated_at)
            VALUES ($1, $2, $3, 'investigating', $4, $5, $6, $7::jsonb, NOW(), NOW())
            ON CONFLICT (incident_id) DO NOTHING;
            """,
            incident_id,
            self.tenant_id,
            alert.title,
            alert.severity.value,
            alert.service,
            fingerprint,
            json.dumps(timeline),
        )

        # 1. Episodic Memory Check (Has this incident fingerprint occurred recently?)
        prior_episode = await self._check_episodic_memory(fingerprint)
        if prior_episode and prior_episode.get("outcome") == "resolved":
            timeline.append({
                "time": datetime.now(UTC).isoformat(),
                "stage": "memory",
                "message": f"Recognized prior resolved episode. Prior fix: {prior_episode.get('brief')}",
            })

        # 2. Evidence Gathering via Telemetry Adapter
        adapter = await get_tenant_telemetry_adapter(self.tenant_id)
        evidence = await adapter.fetch_evidence(service=alert.service, lookback_minutes=15)
        timeline.append({
            "time": datetime.now(UTC).isoformat(),
            "stage": "evidence",
            "message": f"Collected {len(evidence)} evidence records from telemetry adapter.",
        })

        # 3. Evidence-Grounded Diagnosis with RAG
        hypotheses = await self.diagnosis_engine.diagnose(
            service=alert.service,
            title=alert.title,
            severity=alert.severity,
            evidence=evidence,
        )
        best_hypothesis = hypotheses[0] if hypotheses else None
        root_cause = best_hypothesis.root_cause_explanation if best_hypothesis else "Inconclusive root cause"
        confidence = best_hypothesis.confidence if best_hypothesis else 0.0

        timeline.append({
            "time": datetime.now(UTC).isoformat(),
            "stage": "diagnosis",
            "message": f"Root cause formulated ({confidence*100:.0f}% confidence): {best_hypothesis.title if best_hypothesis else 'N/A'}",
        })

        # 4. Deterministic Policy Gate
        if best_hypothesis:
            proposal = await self.policy_gate.evaluate_action(
                incident_id=incident_id,
                service=alert.service,
                severity=alert.severity,
                hypothesis=best_hypothesis,
            )
        else:
            proposal = ActionProposal(
                incident_id=incident_id,
                tenant_id=self.tenant_id,
                tier=ActionTier.TIER_3_ESCALATE,
                action_type="escalate_to_human",
                target_service=alert.service,
                proposal_hash="none",
                status="escalated",
            )

        # 5. Execution & Verification Branching
        action_summary = best_hypothesis.recommended_action if best_hypothesis else "Human investigation required."
        if proposal.tier == ActionTier.TIER_1_AUTO and proposal.status == "approved":
            # Auto-remediation permitted
            timeline.append({
                "time": datetime.now(UTC).isoformat(),
                "stage": "executing",
                "message": f"Auto-remediation executed: {proposal.action_type}",
            })
            outcome = await self.verifier.verify_recovery(incident_id, alert.service, wait_seconds=1.0)
            timeline.append({
                "time": datetime.now(UTC).isoformat(),
                "stage": "verifying",
                "message": f"Recovery verifier outcome: {outcome.value.upper()}",
            })
            incident_status = IncidentStatus.RESOLVED if outcome == VerifierOutcome.RECOVERED else IncidentStatus.ESCALATED
        elif proposal.tier == ActionTier.TIER_2_APPROVAL:
            incident_status = IncidentStatus.AWAITING_APPROVAL
            timeline.append({
                "time": datetime.now(UTC).isoformat(),
                "stage": "awaiting_approval",
                "message": f"Remediation requires SRE approval click (hash: {proposal.proposal_hash[:8]}).",
            })
        else:
            incident_status = IncidentStatus.ESCALATED
            timeline.append({
                "time": datetime.now(UTC).isoformat(),
                "stage": "escalated",
                "message": "Critical severity or high blast-radius: escalated to on-call SRE.",
            })

        # 6. Save Incident to PostgreSQL
        await self._save_incident(
            incident_id=incident_id,
            title=alert.title,
            status=incident_status,
            severity=alert.severity,
            service=alert.service,
            fingerprint=fingerprint,
            root_cause=root_cause,
            confidence=confidence,
            hypotheses=hypotheses,
            evidence=evidence,
            timeline=timeline,
        )

        # 7. Record Episodic Memory
        await self._record_episode(
            fingerprint=fingerprint,
            anomaly_type=alert.anomaly_type,
            severity=alert.severity.value,
            services=[alert.service],
            outcome=incident_status.value,
            brief=action_summary,
        )

        # 8. Produce Final SRE Brief
        brief = SREBrief(
            incident_id=incident_id,
            tenant_id=self.tenant_id,
            title=alert.title,
            severity=alert.severity,
            service=alert.service,
            root_cause=root_cause,
            confidence=confidence,
            fix_applied_or_recommended=action_summary,
            evidence_citations=best_hypothesis.supporting_evidence_ids if best_hypothesis else [],
            action_tier=proposal.tier,
            status=incident_status,
            timeline_summary=[t["message"] for t in timeline],
        )

        record = IncidentRecord(
            incident_id=incident_id,
            tenant_id=self.tenant_id,
            title=alert.title,
            status=incident_status,
            severity=alert.severity,
            service=alert.service,
            fingerprint=fingerprint,
            root_cause=root_cause,
            confidence=confidence,
            hypotheses=hypotheses,
            evidence_summary=evidence,
            timeline=timeline,
        )

        return record, brief

    async def _check_episodic_memory(self, fingerprint: str) -> dict[str, Any] | None:
        """Lookup recent episode for this tenant."""
        query = """
            SELECT outcome, recovery_status, brief
            FROM episodes
            WHERE tenant_id = $1 AND fingerprint = $2
            ORDER BY created_at DESC
            LIMIT 1
        """
        return await fetch_one(query, self.tenant_id, fingerprint)

    async def _record_episode(
        self,
        fingerprint: str,
        anomaly_type: str,
        severity: str,
        services: list[str],
        outcome: str,
        brief: str,
    ) -> None:
        """Save episode outcome for cross-incident learning."""
        episode_id = f"ep-{uuid4().hex[:10]}"
        query = """
            INSERT INTO episodes (
                episode_id, tenant_id, fingerprint, anomaly_type, severity, services, outcome, brief, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6::jsonb, $7, $8, NOW()
            )
        """
        await execute(
            query,
            episode_id,
            self.tenant_id,
            fingerprint,
            anomaly_type,
            severity,
            json.dumps(services),
            outcome,
            brief,
        )

    async def _save_incident(
        self,
        incident_id: str,
        title: str,
        status: IncidentStatus,
        severity: Severity,
        service: str,
        fingerprint: str,
        root_cause: str,
        confidence: float,
        hypotheses: list[Hypothesis],
        evidence: list[EvidenceRecord],
        timeline: list[dict[str, Any]],
    ) -> None:
        """Persist incident record in PostgreSQL."""
        query = """
            INSERT INTO incidents (
                incident_id, tenant_id, title, status, severity, service,
                fingerprint, root_cause, confidence, hypotheses, evidence_summary,
                timeline, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11::jsonb, $12::jsonb, NOW(), NOW()
            )
            ON CONFLICT (incident_id) DO UPDATE SET
                title = EXCLUDED.title,
                status = EXCLUDED.status,
                severity = EXCLUDED.severity,
                service = EXCLUDED.service,
                fingerprint = EXCLUDED.fingerprint,
                root_cause = EXCLUDED.root_cause,
                confidence = EXCLUDED.confidence,
                hypotheses = EXCLUDED.hypotheses,
                evidence_summary = EXCLUDED.evidence_summary,
                timeline = EXCLUDED.timeline,
                updated_at = NOW();
        """
        await execute(
            query,
            incident_id,
            self.tenant_id,
            title,
            status.value,
            severity.value,
            service,
            fingerprint,
            root_cause,
            confidence,
            json.dumps([h.model_dump() for h in hypotheses]),
            json.dumps([e.model_dump() for e in evidence]),
            json.dumps(timeline),
        )
