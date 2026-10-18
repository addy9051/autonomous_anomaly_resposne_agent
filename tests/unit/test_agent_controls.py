"""Tests that dashboard autonomy controls constrain action execution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agents.action.agent import ActionAgent
from agents.action.policy import PolicyEvaluationResult
from shared.schemas import (
    ActionPolicyDecision,
    ActionTier,
    DiagnosisResult,
    IncidentRecord,
    RecommendedAction,
    RootCauseCategory,
)


@pytest.mark.asyncio
async def test_service_modes_and_global_pause_never_bypass_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = object.__new__(ActionAgent)
    agent.settings = SimpleNamespace(app=SimpleNamespace(app_env="development"))
    workflow = AsyncMock(return_value={"status": "executed"})
    monkeypatch.setattr("agents.action.agent.trigger_workflow", workflow)
    monkeypatch.setattr(
        "agents.action.agent.DeterministicActionPolicy.evaluate",
        lambda **kwargs: PolicyEvaluationResult(
            decision=ActionPolicyDecision.PERMITTED_AUTO,
            reason="permitted by baseline test policy",
            requires_approval=False,
            validated_params={"replicas": 3},
            is_simulated=True,
        ),
    )
    diagnosis = DiagnosisResult(
        event_id="event",
        root_cause="test",
        root_cause_category=RootCauseCategory.UNKNOWN,
        recommended_actions=[
            RecommendedAction(action="scale_replicas", tier=ActionTier.TIER_1_AUTO, params={"replicas": 3})
        ],
        confidence=0.8,
    )
    incident = IncidentRecord(incident_id="incident")

    suggested = await agent._evaluate_and_execute_action(
        diagnosis.recommended_actions[0], diagnosis, incident, "checkout", True, autonomy="suggest"
    )
    approval_required = await agent._evaluate_and_execute_action(
        diagnosis.recommended_actions[0], diagnosis, incident, "checkout", True, autonomy="approve_to_act"
    )
    paused = await agent._evaluate_and_execute_action(
        diagnosis.recommended_actions[0], diagnosis, incident, "checkout", True, paused=True
    )

    assert suggested.execution_status == "recommend_only"
    assert approval_required.execution_status == "pending_approval"
    assert approval_required.proposal_hash
    assert paused.execution_status == "recommend_only"
    workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_model_action_is_denied_and_model_tier_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    agent = object.__new__(ActionAgent)
    agent.settings = SimpleNamespace(app=SimpleNamespace(app_env="development"))
    workflow = AsyncMock(return_value={"status": "executed"})
    monkeypatch.setattr("agents.action.agent.trigger_workflow", workflow)
    action = RecommendedAction(
        action="check_slow_query_traces",
        tier=ActionTier.TIER_1_AUTO,
        params={},
    )
    diagnosis = DiagnosisResult(
        event_id="event",
        root_cause="test",
        root_cause_category=RootCauseCategory.UNKNOWN,
        recommended_actions=[action],
        confidence=0.8,
    )

    result = await agent._evaluate_and_execute_action(
        action,
        diagnosis,
        IncidentRecord(incident_id="incident"),
        "checkout",
        True,
    )

    assert result.execution_status == "policy_denied"
    assert result.policy_decision == ActionPolicyDecision.DENIED
    assert result.tier == ActionTier.TIER_3_HUMAN
    workflow.assert_not_awaited()
