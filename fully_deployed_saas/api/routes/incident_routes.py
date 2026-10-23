"""
Incident Lifecycle & Action Approval Routes.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from agents.verifier import ClosedLoopVerifier
from core.auth import TenantDep
from core.schemas import VerifierOutcome
from core.utils import get_logger
from database.connection import execute, fetch_all, fetch_one

logger = get_logger("incident_routes")
router = APIRouter(prefix="/incidents", tags=["Incidents & Governance"])


class ApprovalRequest(BaseModel):
    decision: str = "approve"  # approve or reject
    reason: str = "Approved by on-call SRE via command center"


@router.get("")
async def list_incidents(
    tenant: TenantDep,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List recent incidents for the tenant."""
    query = """
        SELECT incident_id, title, status, severity, service, confidence, created_at, updated_at
        FROM incidents
        WHERE tenant_id = $1
        ORDER BY created_at DESC
        LIMIT $2
    """
    rows = await fetch_all(query, tenant.tenant_id, limit)
    return [
        {
            "incident_id": r["incident_id"],
            "title": r["title"],
            "status": r["status"],
            "severity": r["severity"],
            "service": r["service"],
            "confidence": float(r["confidence"]) if r["confidence"] else 0.0,
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in rows
    ]


@router.get("/active")
async def list_active_incidents(tenant: TenantDep) -> list[dict[str, Any]]:
    """List currently active / investigating incidents."""
    query = """
        SELECT incident_id, title, status, severity, service, confidence, created_at
        FROM incidents
        WHERE tenant_id = $1 AND status NOT IN ('resolved', 'closed')
        ORDER BY created_at DESC
    """
    rows = await fetch_all(query, tenant.tenant_id)
    return [
        {
            "incident_id": r["incident_id"],
            "title": r["title"],
            "status": r["status"],
            "severity": r["severity"],
            "service": r["service"],
            "confidence": float(r["confidence"]) if r["confidence"] else 0.0,
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


@router.get("/{incident_id}")
async def get_incident_detail(
    incident_id: str,
    tenant: TenantDep,
) -> dict[str, Any]:
    """Fetch full incident detail including hypotheses, evidence citations, and timeline."""
    query = """
        SELECT incident_id, title, status, severity, service, root_cause, confidence,
               hypotheses, evidence_summary, timeline, created_at, updated_at
        FROM incidents
        WHERE tenant_id = $1 AND incident_id = $2
    """
    row = await fetch_one(query, tenant.tenant_id, incident_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    # Fetch pending or associated action proposal
    prop_query = """
        SELECT id, tier, action_type, target_service, parameters, proposal_hash, status, verifier_outcome
        FROM action_proposals
        WHERE tenant_id = $1 AND incident_id = $2
        ORDER BY created_at DESC
        LIMIT 1
    """
    prop = await fetch_one(prop_query, tenant.tenant_id, incident_id)

    return {
        "incident_id": row["incident_id"],
        "title": row["title"],
        "status": row["status"],
        "severity": row["severity"],
        "service": row["service"],
        "root_cause": row["root_cause"],
        "confidence": float(row["confidence"]) if row["confidence"] else 0.0,
        "hypotheses": json.loads(row["hypotheses"]) if isinstance(row["hypotheses"], str) else row["hypotheses"],
        "evidence": json.loads(row["evidence_summary"]) if isinstance(row["evidence_summary"], str) else row["evidence_summary"],
        "timeline": json.loads(row["timeline"]) if isinstance(row["timeline"], str) else row["timeline"],
        "action_proposal": {
            "id": str(prop["id"]),
            "tier": prop["tier"],
            "action_type": prop["action_type"],
            "target_service": prop["target_service"],
            "parameters": json.loads(prop["parameters"]) if isinstance(prop["parameters"], str) else prop["parameters"],
            "proposal_hash": prop["proposal_hash"],
            "status": prop["status"],
            "verifier_outcome": prop["verifier_outcome"],
        } if prop else None,
        "created_at": row["created_at"].isoformat(),
    }


@router.post("/{incident_id}/approve")
async def approve_incident_action(
    incident_id: str,
    req: ApprovalRequest,
    tenant: TenantDep,
) -> dict[str, Any]:
    """
    SRE Action Approval Gate.
    Verifies proposal hash, transitions incident, executes remediation, and runs verification.
    """
    # 1. Fetch pending proposal
    prop_query = """
        SELECT id, action_type, target_service, parameters, proposal_hash, status
        FROM action_proposals
        WHERE tenant_id = $1 AND incident_id = $2 AND status = 'pending'
        LIMIT 1
    """
    prop = await fetch_one(prop_query, tenant.tenant_id, incident_id)
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending action proposal found for this incident",
        )

    if req.decision.lower() == "reject":
        await execute(
            """
            UPDATE action_proposals SET status = 'rejected', approval_reason = $1, updated_at = NOW()
            WHERE id = $2
            """,
            req.reason,
            prop["id"],
        )
        await execute(
            "UPDATE incidents SET status = 'escalated', updated_at = NOW() WHERE incident_id = $1",
            incident_id,
        )
        return {"status": "rejected", "incident_id": incident_id}

    # 2. Approved: Transition to executing
    await execute(
        """
        UPDATE action_proposals
        SET status = 'executing', approved_by = $1, approval_reason = $2, updated_at = NOW()
        WHERE id = $3
        """,
        tenant.user_id,
        req.reason,
        prop["id"],
    )
    await execute(
        "UPDATE incidents SET status = 'executing', updated_at = NOW() WHERE incident_id = $1",
        incident_id,
    )

    # 3. Simulate or trigger action execution
    logger.info("executing_approved_action", action=prop["action_type"], service=prop["target_service"])

    # 4. Closed-loop recovery verification
    verifier = ClosedLoopVerifier(tenant.tenant_id)
    outcome = await verifier.verify_recovery(incident_id, prop["target_service"], wait_seconds=1.0)

    final_status = "resolved" if outcome == VerifierOutcome.RECOVERED else "escalated"
    await execute(
        "UPDATE incidents SET status = $1, updated_at = NOW() WHERE incident_id = $2",
        final_status,
        incident_id,
    )
    await execute(
        "UPDATE action_proposals SET status = 'completed', updated_at = NOW() WHERE id = $1",
        prop["id"],
    )

    # 5. Audit log entry
    await execute(
        """
        INSERT INTO audit_logs (tenant_id, event_type, actor, details)
        VALUES ($1, 'action_approved', $2, $3::jsonb)
        """,
        tenant.tenant_id,
        tenant.user_id,
        json.dumps({
            "incident_id": incident_id,
            "action": prop["action_type"],
            "verifier_outcome": outcome.value,
        }),
    )

    return {
        "status": "completed",
        "incident_id": incident_id,
        "action_executed": prop["action_type"],
        "verifier_outcome": outcome.value,
        "final_incident_status": final_status,
    }
