"""
Integration Tests — Blueprint Lifecycle & Safety Verification.

Validates the full incident lifecycle against SRE Agent Product Strategy Blueprint:
1. Approval actually executes the selected action with independent verification.
2. Rejection records denial, does not execute, and does not resolve.
3. Durable persistence preserves pending approvals across process restarts.
4. Failed or inconclusive recovery verification prevents resolution and escalates.
5. Malformed diagnosis cannot create an executable write action (no action-shaped fallback).
6. Telemetry source failures remain visible as evidence gaps.
7. Production environment fails closed (writes require explicit approval).
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.action.agent import ActionAgent
from agents.action.policy import DeterministicActionPolicy
from agents.diagnosis.graph import DiagnosisAgent, _parse_diagnosis_response
from shared.incident_store import IncidentStore
from shared.schemas import (
    ActionPolicyDecision,
    ActionResult,
    ActionTier,
    AnomalyEvent,
    DiagnosisResult,
    IncidentRecord,
    IncidentStatus,
    RecommendedAction,
    RootCauseCategory,
    Severity,
    VerificationOutcome,
    VerificationResult,
)


@pytest.fixture
def sample_anomaly_event() -> AnomalyEvent:
    return AnomalyEvent(
        event_id="test-event-uuid-1234",
        timestamp=datetime.now(timezone.utc),
        severity=Severity.HIGH,
        affected_services=["payment-gateway"],
        anomaly_type="latency_spike",
        metrics_snapshot={"p99_latency_ms": 1500, "error_rate": 0.05},
        reasoning="Mocked anomaly detection triggered.",
        confidence=0.95,
    )


@pytest.mark.asyncio
async def test_end_to_end_mocked_incident_flow(sample_anomaly_event: AnomalyEvent) -> None:
    """
    Simulates a full end-to-end integration test with independent verification.
    """
    with (
        patch("agents.diagnosis.graph.build_diagnosis_graph") as mock_build,
        patch("agents.action.agent.get_chat_model") as mock_action_llm,
        patch("agents.action.agent.trigger_workflow") as mock_trigger_workflow,
        patch("agents.verification.verifier.RecoveryVerifier.verify") as mock_verify,
    ):
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(
            return_value={
                "diagnosis_result": {
                    "root_cause": "Database connection pool exhaustion",
                    "root_cause_category": "database",
                    "decision_summary": "Identified connection pool exhaustion on payment-db",
                    "runbook_references": [],
                    "recommended_actions": [{"action": "scale_replicas", "tier": 1, "params": {"replicas": 3}}],
                    "reasoning_chain": "Observed high connection wait times",
                    "confidence": 0.95,
                }
            }
        )
        mock_build.return_value = mock_graph

        mock_action_instance = MagicMock()
        mock_action_instance.ainvoke = AsyncMock(
            return_value=MagicMock(content="Incident summary: Replicas scaled. Recovery verified.")
        )
        mock_action_llm.return_value = mock_action_instance
        mock_trigger_workflow.return_value = {"status": "success", "execution_id": "999"}

        # Independent verifier confirms recovery
        mock_verify.return_value = VerificationResult(
            outcome=VerificationOutcome.VERIFIED_RECOVERY,
            checked_signals={"error_rate": 0.005, "latency_p99": 110.0},
            details="All SLIs returned to normal baseline",
        )

        diagnosis_agent = DiagnosisAgent()
        diagnosis_result = await diagnosis_agent.diagnose(sample_anomaly_event)

        assert diagnosis_result.event_id == "test-event-uuid-1234"
        assert diagnosis_result.root_cause_category == RootCauseCategory.DATABASE

        action_agent = ActionAgent()
        incident = IncidentRecord(
            incident_id=diagnosis_result.incident_id,
            status=IncidentStatus.INVESTIGATING,
            anomaly_event=sample_anomaly_event,
            diagnosis_result=diagnosis_result,
        )

        action_results = await action_agent.execute(diagnosis_result, incident, dry_run=True)

        assert incident.status == IncidentStatus.VERIFIED_RECOVERED
        assert incident.auto_resolved is True
        assert action_results[0].action_taken == "scale_replicas"
        mock_trigger_workflow.assert_called_once()
        mock_verify.assert_called_once()


@pytest.mark.asyncio
async def test_approval_executes_action_and_verifies_recovery() -> None:
    """P0 Test: Approval executes the exact action and runs independent verification."""
    action_agent = ActionAgent()
    incident = IncidentRecord(
        incident_id="inc-approval-1",
        status=IncidentStatus.AWAITING_APPROVAL,
    )

    with (
        patch("agents.action.agent.trigger_workflow") as mock_workflow,
        patch("agents.verification.verifier.RecoveryVerifier.verify") as mock_verify,
    ):
        mock_workflow.return_value = {"status": "success", "output": "drained"}
        mock_verify.return_value = VerificationResult(
            outcome=VerificationOutcome.VERIFIED_RECOVERY,
            details="Recovery independently verified by SLI check",
        )

        result = await action_agent.execute_approved_action(
            action_name="drain_node",
            params={"node_id": "node-1"},
            incident=incident,
            approver="alice@sre.company",
            dry_run=True,
        )

        assert result.human_approved is True
        assert result.approver == "alice@sre.company"
        assert result.execution_status == "success"
        assert incident.status == IncidentStatus.VERIFIED_RECOVERED
        mock_workflow.assert_called_once_with("drain_node", {"node_id": "node-1"}, dry_run=True)
        mock_verify.assert_called_once()


@pytest.mark.asyncio
async def test_rejection_does_not_execute_or_resolve(tmp_path: Path) -> None:
    """P0 Test: Rejection records denial, does not trigger workflow, and does not resolve."""
    from api import ApproveActionRequest, approve_tier2_action
    import api

    store = IncidentStore(db_path=tmp_path / "test_incidents.db")
    incident = IncidentRecord(
        incident_id="inc-reject-1",
        status=IncidentStatus.AWAITING_APPROVAL,
        action_results=[
            ActionResult(
                incident_id="inc-reject-1",
                action_taken="failover_db",
                tier=ActionTier.TIER_2_APPROVE,
                execution_status="pending_approval",
            )
        ],
    )
    store.save(incident)

    mock_orch = MagicMock()
    mock_orch.active_incidents = {"inc-reject-1": incident}
    mock_orch.resolved_incidents = []
    mock_orch.store = store

    api.orchestrator = mock_orch

    with patch("agents.action.agent.trigger_workflow") as mock_workflow:
        req = ApproveActionRequest(
            approved=False,
            approver="bob@sre.company",
            reason="Unsafe timing during peak traffic",
        )
        resp = await approve_tier2_action("inc-reject-1", req, _auth=None)

        assert resp["status"] == "rejected"
        assert resp["approved"] is False
        assert incident.status == IncidentStatus.ESCALATED
        assert incident.human_overrode is True
        mock_workflow.assert_not_called()

        # Check durable store has recorded rejection
        persisted = store.get("inc-reject-1")
        assert persisted is not None
        assert persisted.status == IncidentStatus.ESCALATED


@pytest.mark.asyncio
async def test_restart_preserves_pending_approvals_durability(tmp_path: Path) -> None:
    """P1 Test: Durable incident store preserves pending approvals across process restarts."""
    db_file = tmp_path / "durability.db"

    # Process 1 creates an incident awaiting approval
    store1 = IncidentStore(db_path=db_file)
    incident = IncidentRecord(
        incident_id="inc-durable-999",
        status=IncidentStatus.AWAITING_APPROVAL,
        action_results=[
            ActionResult(
                incident_id="inc-durable-999",
                action_taken="drain_node",
                tier=ActionTier.TIER_2_APPROVE,
                execution_status="pending_approval",
                output={"recommended_params": {"node_id": "worker-4"}},
            )
        ],
    )
    store1.save(incident)

    # Process 2 starts up fresh with new store instance connected to the same db
    store2 = IncidentStore(db_path=db_file)
    active = store2.list_active()

    assert any(i.incident_id == "inc-durable-999" for i in active)
    loaded = store2.get("inc-durable-999")
    assert loaded is not None
    assert loaded.status == IncidentStatus.AWAITING_APPROVAL
    assert loaded.action_results[0].action_taken == "drain_node"


@pytest.mark.asyncio
async def test_failed_or_inconclusive_verification_escalates() -> None:
    """P0 Test: Failed or inconclusive verification does not resolve the incident."""
    action_agent = ActionAgent()
    incident = IncidentRecord(
        incident_id="inc-inconclusive-1",
        status=IncidentStatus.AWAITING_APPROVAL,
    )

    with (
        patch("agents.action.agent.trigger_workflow") as mock_workflow,
        patch("agents.verification.verifier.RecoveryVerifier.verify") as mock_verify,
    ):
        mock_workflow.return_value = {"status": "success"}

        # Verifier reports telemetry unavailable / inconclusive
        mock_verify.return_value = VerificationResult(
            outcome=VerificationOutcome.INCONCLUSIVE,
            details="Telemetry source timed out; post-action health cannot be confirmed",
        )

        await action_agent.execute_approved_action(
            action_name="clear_cache",
            params={},
            incident=incident,
            dry_run=True,
        )

        assert incident.status == IncidentStatus.ESCALATED
        assert incident.auto_resolved is False


def test_malformed_diagnosis_cannot_create_write_action() -> None:
    """P0 Test: Malformed LLM output results in non-actionable state, never an executable action."""
    state = {
        "anomaly_event": {"event_id": "ev-1"},
        "context": "",
        "runbook_matches": [],
        "required_experts": [],
        "sub_agent_reports": {},
        "evidence_timeline": [],
        "missing_sources": [],
        "diagnosis_result": None,
        "messages": [],
    }

    # Malformed non-JSON output
    malformed_output = "I cannot determine the root cause, maybe scale replicas? ```json { invalid"
    result = _parse_diagnosis_response(malformed_output, {"event_id": "ev-1"}, state)

    assert result["root_cause"].startswith("diagnosis_unavailable")
    assert result["confidence"] == 0.0
    # Must NOT have any executable actions!
    assert len(result["recommended_actions"]) == 0


def test_production_writes_fail_closed_and_require_approval() -> None:
    """P0 Test: Production writes are never autonomously executable; parameter bounds are strictly enforced."""
    # 1. Low risk action in production -> Requires human approval
    eval_prod = DeterministicActionPolicy.evaluate(
        action_name="scale_replicas",
        service="checkout",
        environment="production",
        params={"replicas": 3},
        dry_run=False,
    )
    assert eval_prod.decision == ActionPolicyDecision.APPROVAL_REQUIRED

    # 2. Parameter boundary violation -> Denied
    eval_invalid_params = DeterministicActionPolicy.evaluate(
        action_name="scale_replicas",
        service="checkout",
        environment="development",
        params={"replicas": 999},  # Exceeds max 10
        dry_run=True,
    )
    assert eval_invalid_params.decision == ActionPolicyDecision.DENIED
    assert "Parameter validation failed" in eval_invalid_params.reason

    # 3. High risk action -> Recommend-only (human-only execution)
    eval_high_risk = DeterministicActionPolicy.evaluate(
        action_name="rollback_deployment",
        service="checkout",
        environment="development",
        params={},
        dry_run=True,
    )
    assert eval_high_risk.decision == ActionPolicyDecision.RECOMMEND_ONLY
