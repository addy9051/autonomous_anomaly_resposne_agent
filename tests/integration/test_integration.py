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

import copy
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from agents.action.agent import ActionAgent
from agents.action.policy import DeterministicActionPolicy, compute_proposal_hash
from agents.diagnosis.graph import DiagnosisAgent, _parse_diagnosis_response
from shared.incident_store import IncidentStore
from shared.schemas import (
    ActionPolicyDecision,
    ActionResult,
    ActionTier,
    AnomalyEvent,
    IncidentRecord,
    IncidentStatus,
    RootCauseCategory,
    Severity,
    VerificationOutcome,
    VerificationResult,
)


@pytest.fixture
def sample_anomaly_event() -> AnomalyEvent:
    return AnomalyEvent(
        event_id="test-event-uuid-1234",
        timestamp=datetime.now(UTC),
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
        mock_graph.aget_state = AsyncMock(return_value=MagicMock(next=()))
        mock_graph.ainvoke = AsyncMock(
            return_value={
                "diagnosis_result": {
                    "root_cause": "Database connection pool exhaustion",
                    "root_cause_category": "database",
                    "decision_summary": "Identified connection pool exhaustion on payment-db",
                    "runbook_references": [],
                    "recommended_actions": [
                        {
                            "action": "scale_replicas",
                            "tier": 1,
                            "params": {"deployment": "payment-gateway", "namespace": "payments", "replicas": 3},
                        }
                    ],
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
        action_agent.settings = copy.deepcopy(action_agent.settings)
        action_agent.settings.app.app_env = "development"
        incident = IncidentRecord(
            incident_id=diagnosis_result.incident_id,
            status=IncidentStatus.INVESTIGATING,
            anomaly_event=sample_anomaly_event,
            diagnosis_result=diagnosis_result,
        )

        with patch("agents.action.policy.get_settings", return_value=action_agent.settings):
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
    action_agent.settings = copy.deepcopy(action_agent.settings)
    action_agent.settings.app.app_env = "production"
    params = {"node_name": "node-1"}
    approval_criteria = {"health_check": True, "error_rate_threshold": 0.01}
    proposal_hash = compute_proposal_hash(
        "drain_node",
        params,
        service="unknown",
        environment="production",
        dry_run=True,
        policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED.value,
        verification_criteria=approval_criteria,
    )
    incident = IncidentRecord(
        incident_id="inc-approval-1",
        status=IncidentStatus.AWAITING_APPROVAL,
        action_results=[
            ActionResult(
                incident_id="inc-approval-1",
                action_taken="drain_node",
                tier=ActionTier.TIER_2_APPROVE,
                execution_status="pending_approval",
                output={"recommended_params": params},
                proposal_hash=proposal_hash,
                is_simulated=True,
                policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                verification_criteria=approval_criteria,
                proposed_service="unknown",
                proposed_environment="production",
            )
        ],
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
            params=params,
            incident=incident,
            approver="alice@sre.company",
            proposal_hash=proposal_hash,
            dry_run=True,
        )

        assert result.human_approved is True
        assert result.approver == "alice@sre.company"
        assert result.execution_status == "success"
        assert incident.status == IncidentStatus.VERIFIED_RECOVERED
        mock_workflow.assert_called_once_with("drain_node", {"node_name": "node-1"}, dry_run=True)
        mock_verify.assert_called_once()


@pytest.mark.asyncio
async def test_rejection_does_not_execute_or_resolve(tmp_path: Path) -> None:
    """P0 Test: Rejection records denial, does not trigger workflow, and does not resolve."""
    import api
    from api import ApproveActionRequest, approve_tier2_action

    params = {"cluster_name": "payments", "replica_id": "replica-2"}
    approval_criteria = {"health_check": True, "error_rate_threshold": 0.01}
    proposal_hash = compute_proposal_hash(
        "failover_db",
        params,
        service="unknown",
        environment="production",
        dry_run=False,
        policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED.value,
        verification_criteria=approval_criteria,
    )
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
                output={"recommended_params": params},
                proposal_hash=proposal_hash,
                policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                verification_criteria=approval_criteria,
                proposed_service="unknown",
                proposed_environment="production",
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
            reason="Unsafe timing during peak traffic",
        )
        resp = await approve_tier2_action("inc-reject-1", req, approver="bob@sre.company")

        assert resp["status"] == "rejected"
        assert resp["approved"] is False
        assert api.orchestrator.active_incidents["inc-reject-1"].status == IncidentStatus.ESCALATED
        assert api.orchestrator.active_incidents["inc-reject-1"].human_overrode is True
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
                output={"recommended_params": {"node_name": "worker-4"}},
                proposal_hash=compute_proposal_hash("drain_node", {"node_name": "worker-4"}),
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
    action_agent.settings = copy.deepcopy(action_agent.settings)
    action_agent.settings.app.app_env = "production"
    params = {"service": "payments", "cache_pattern": "checkout:*"}
    approval_criteria = {"health_check": True, "error_rate_threshold": 0.01}
    proposal_hash = compute_proposal_hash(
        "clear_cache",
        params,
        service="unknown",
        environment="production",
        dry_run=True,
        policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED.value,
        verification_criteria=approval_criteria,
    )
    incident = IncidentRecord(
        incident_id="inc-inconclusive-1",
        status=IncidentStatus.AWAITING_APPROVAL,
        action_results=[
            ActionResult(
                incident_id="inc-inconclusive-1",
                action_taken="clear_cache",
                tier=ActionTier.TIER_1_AUTO,
                execution_status="pending_approval",
                output={"recommended_params": params},
                proposal_hash=proposal_hash,
                is_simulated=True,
                policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                verification_criteria=approval_criteria,
                proposed_service="unknown",
                proposed_environment="production",
            )
        ],
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
            params=params,
            incident=incident,
            approver="alice@sre.company",
            proposal_hash=proposal_hash,
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
        params={"deployment": "checkout", "namespace": "payments", "replicas": 3},
        dry_run=False,
    )
    assert eval_prod.decision == ActionPolicyDecision.APPROVAL_REQUIRED

    # 2. Parameter boundary violation -> Denied
    eval_invalid_params = DeterministicActionPolicy.evaluate(
        action_name="scale_replicas",
        service="checkout",
        environment="development",
        params={"deployment": "checkout", "namespace": "payments", "replicas": 999},
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


def test_approval_request_rejects_action_substitution() -> None:
    """Caller-supplied action fields are not accepted by the approval API contract."""
    from api import ApproveActionRequest

    with pytest.raises(ValidationError):
        ApproveActionRequest.model_validate({})

    with pytest.raises(ValidationError):
        ApproveActionRequest.model_validate(
            {
                "approved": True,
                "action_name": "failover_db",
                "params": {"cluster_name": "production"},
            }
        )


def test_approval_claim_is_single_use(tmp_path: Path) -> None:
    """Persisted proposal can be claimed once and cannot be replayed."""
    store = IncidentStore(db_path=tmp_path / "single_use.db")
    params = {"deployment": "checkout", "namespace": "payments", "replicas": 3}
    proposal_hash = compute_proposal_hash("scale_replicas", params)
    store.save(
        IncidentRecord(
            incident_id="inc-single-use",
            status=IncidentStatus.AWAITING_APPROVAL,
            action_results=[
                ActionResult(
                    incident_id="inc-single-use",
                    action_taken="scale_replicas",
                    tier=ActionTier.TIER_1_AUTO,
                    execution_status="pending_approval",
                    output={"recommended_params": params},
                    proposal_hash=proposal_hash,
                )
            ],
        )
    )

    claimed = store.claim_pending_approval(
        "inc-single-use",
        proposal_hash,
        approved=True,
        actor="arn:aws:iam::123456789012:role/sre",
    )
    replay = store.claim_pending_approval(
        "inc-single-use",
        proposal_hash,
        approved=True,
        actor="arn:aws:iam::123456789012:role/sre",
    )

    assert claimed is not None
    assert claimed.status == IncidentStatus.EXECUTING
    assert replay is None


def test_sensitive_post_routes_require_auth_dependency() -> None:
    import api

    expected_paths = {
        "/api/v1/events/process",
        "/api/v1/demo/run",
        "/api/v1/demo/scenarios/{scenario_name}/run",
        "/api/v1/stream/start",
        "/api/v1/incidents/{incident_id}/approve",
        "/api/v1/knowledge/seed",
    }
    protected_paths = {
        route.path
        for route in api.app.routes
        if "POST" in getattr(route, "methods", set())
        and any(dependency.call is api.verify_auth for dependency in route.dependant.dependencies)
    }

    assert expected_paths <= protected_paths


@pytest.mark.asyncio
async def test_authentication_requires_gateway_identity_outside_development() -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException
    from starlette.requests import Request

    import api

    request = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    settings = SimpleNamespace(
        app=SimpleNamespace(
            app_env="production",
            trusted_identity_header="X-Authenticated-User",
            require_gateway_auth=False,
            api_auth_token="",
        )
    )

    with patch("api.get_settings", return_value=settings), pytest.raises(HTTPException) as exc_info:
        await api.verify_auth(request, api_key=None)

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_authentication_uses_gateway_identity() -> None:
    from types import SimpleNamespace

    from starlette.requests import Request

    import api

    request = Request(
        {
            "type": "http",
            "headers": [(b"x-authenticated-user", b"arn:aws:iam::123456789012:role/sre")],
            "method": "POST",
            "path": "/",
        }
    )
    settings = SimpleNamespace(
        app=SimpleNamespace(
            app_env="production",
            trusted_identity_header="X-Authenticated-User",
            require_gateway_auth=False,
            api_auth_token="",
        )
    )

    with patch("api.get_settings", return_value=settings):
        identity = await api.verify_auth(request, api_key=None)

    assert identity == "arn:aws:iam::123456789012:role/sre"


@pytest.mark.asyncio
async def test_approval_policy_recheck_blocks_execution() -> None:
    from agents.action.policy import PolicyEvaluationResult

    action_agent = ActionAgent()
    action_agent.settings = copy.deepcopy(action_agent.settings)
    action_agent.settings.app.app_env = "production"
    params = {"node_name": "node-1"}
    approval_criteria = {"health_check": True, "error_rate_threshold": 0.01}
    proposal_hash = compute_proposal_hash(
        "drain_node",
        params,
        service="unknown",
        environment="production",
        dry_run=True,
        policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED.value,
        verification_criteria=approval_criteria,
    )
    incident = IncidentRecord(
        incident_id="inc-policy-recheck",
        status=IncidentStatus.EXECUTING,
        action_results=[
            ActionResult(
                incident_id="inc-policy-recheck",
                action_taken="drain_node",
                tier=ActionTier.TIER_2_APPROVE,
                execution_status="pending_approval",
                output={"recommended_params": params},
                proposal_hash=proposal_hash,
                is_simulated=True,
                policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                verification_criteria=approval_criteria,
                proposed_service="unknown",
                proposed_environment="production",
            )
        ],
    )

    denied = PolicyEvaluationResult(
        decision=ActionPolicyDecision.DENIED,
        reason="Target no longer satisfies policy.",
        requires_approval=True,
        validated_params=params,
    )
    with (
        patch("agents.action.agent.DeterministicActionPolicy.evaluate", return_value=denied),
        patch("agents.action.agent.trigger_workflow") as mock_workflow,
    ):
        result = await action_agent.execute_approved_action(
            action_name="drain_node",
            params=params,
            incident=incident,
            approver="arn:aws:iam::123456789012:role/sre",
            proposal_hash=proposal_hash,
            dry_run=True,
        )

    assert result.execution_status == "policy_denied"
    assert incident.status == IncidentStatus.ESCALATED
    mock_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_approval_cannot_cross_environment_boundary() -> None:
    action_agent = ActionAgent()
    action_agent.settings = copy.deepcopy(action_agent.settings)
    action_agent.settings.app.app_env = "production"
    params = {"node_name": "node-1"}
    criteria = {"health_check": True}
    proposal_hash = compute_proposal_hash(
        "drain_node",
        params,
        service="unknown",
        environment="development",
        dry_run=False,
        policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED.value,
        verification_criteria=criteria,
    )
    incident = IncidentRecord(
        incident_id="inc-env-boundary",
        status=IncidentStatus.EXECUTING,
        action_results=[
            ActionResult(
                incident_id="inc-env-boundary",
                action_taken="drain_node",
                tier=ActionTier.TIER_2_APPROVE,
                execution_status="pending_approval",
                output={"recommended_params": params},
                policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                proposal_hash=proposal_hash,
                verification_criteria=criteria,
                proposed_service="unknown",
                proposed_environment="development",
            )
        ],
    )

    with (
        patch("agents.action.agent.trigger_workflow") as mock_workflow,
        pytest.raises(ValueError, match="service or environment"),
    ):
        await action_agent.execute_approved_action(
            action_name="drain_node",
            params=params,
            incident=incident,
            approver="arn:aws:iam::123456789012:role/sre",
            proposal_hash=proposal_hash,
            dry_run=False,
        )

    mock_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_approval_api_executes_only_persisted_proposal(tmp_path: Path) -> None:
    import api
    from api import ApproveActionRequest, approve_tier2_action

    params = {"deployment": "checkout", "namespace": "payments", "replicas": 3}
    approval_criteria = {"health_check": True, "error_rate_threshold": 0.01}
    proposal_hash = compute_proposal_hash(
        "scale_replicas",
        params,
        service="checkout",
        environment="production",
        dry_run=True,
        policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED.value,
        verification_criteria=approval_criteria,
    )
    proposal_hash_expected = proposal_hash
    store = IncidentStore(db_path=tmp_path / "approval_api.db")
    incident = IncidentRecord(
        incident_id="inc-bound-proposal",
        status=IncidentStatus.AWAITING_APPROVAL,
        action_results=[
            ActionResult(
                incident_id="inc-bound-proposal",
                action_taken="scale_replicas",
                tier=ActionTier.TIER_1_AUTO,
                execution_status="pending_approval",
                output={"recommended_params": params},
                proposal_hash=proposal_hash,
                is_simulated=True,
                policy_decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                verification_criteria=approval_criteria,
                proposed_service="checkout",
                proposed_environment="production",
            )
        ],
    )
    store.save(incident)

    async def execute(
        *,
        action_name: str,
        params: dict[str, object],
        proposal_hash: str,
        dry_run: bool,
        incident: IncidentRecord,
        approver: str,
    ) -> ActionResult:
        assert action_name == "scale_replicas"
        assert params == {"deployment": "checkout", "namespace": "payments", "replicas": 3}
        assert proposal_hash == proposal_hash_expected
        assert dry_run is True
        assert approver == "arn:aws:iam::123456789012:role/sre"
        incident.status = IncidentStatus.ESCALATED
        return ActionResult(
            incident_id=incident.incident_id,
            action_taken=action_name,
            tier=ActionTier.TIER_1_AUTO,
            execution_status="policy_denied",
        )

    mock_orch = MagicMock()
    mock_orch.active_incidents = {"inc-bound-proposal": incident}
    mock_orch.resolved_incidents = []
    mock_orch.store = store
    mock_orch.action_agent.execute_approved_action = AsyncMock(side_effect=execute)
    api.orchestrator = mock_orch

    response = await approve_tier2_action(
        "inc-bound-proposal",
        ApproveActionRequest(approved=True),
        approver="arn:aws:iam::123456789012:role/sre",
    )

    assert response["action_taken"] == "scale_replicas"
    assert mock_orch.action_agent.execute_approved_action.await_count == 1
    assert api.orchestrator.active_incidents["inc-bound-proposal"].status == IncidentStatus.ESCALATED
