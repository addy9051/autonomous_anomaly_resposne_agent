"""
Unit tests for the reward function and action tiers.
"""

from __future__ import annotations

from typing import Any

import pytest

from agents.action.tiers import (
    ACTION_TIERS,
    classify_action,
    get_tier_actions,
    get_tier_description,
)
from agents.feedback.reward import compute_reward
from shared.schemas import (
    ActionTier,
    IncidentRecord,
    IncidentStatus,
    SemanticReward,
    VerificationOutcome,
    VerificationResult,
)


class TestActionTiers:
    """Tests for action tier classification."""

    def test_tier1_actions(self) -> None:
        tier1 = get_tier_actions(ActionTier.TIER_1_AUTO)
        assert "scale_replicas" in tier1
        assert "clear_cache" in tier1
        assert "restart_unhealthy_pod" in tier1

    def test_tier2_actions(self) -> None:
        tier2 = get_tier_actions(ActionTier.TIER_2_APPROVE)
        assert "drain_node" in tier2
        assert "failover_db" in tier2

    def test_tier3_actions(self) -> None:
        tier3 = get_tier_actions(ActionTier.TIER_3_HUMAN)
        assert "rollback_deployment" in tier3
        assert "block_issuer" in tier3

    def test_unknown_action_defaults_to_tier3(self) -> None:
        """Unknown actions should always require human intervention."""
        assert classify_action("unknown_dangerous_action") == ActionTier.TIER_3_HUMAN
        assert classify_action("delete_production_database") == ActionTier.TIER_3_HUMAN

    def test_all_actions_have_tiers(self) -> None:
        """All defined actions should have a valid tier."""
        for action, tier in ACTION_TIERS.items():
            assert tier in ActionTier
            assert isinstance(action, str)

    def test_tier_descriptions(self) -> None:
        assert "Autonomous" in get_tier_description(ActionTier.TIER_1_AUTO)
        assert "Approval" in get_tier_description(ActionTier.TIER_2_APPROVE)
        assert "Human" in get_tier_description(ActionTier.TIER_3_HUMAN)


class TestRewardFunction:
    """Table-driven tests for the reward function."""

    def _make_incident(self, **kwargs: Any) -> IncidentRecord:  # noqa: ANN401
        """Create a minimal incident for testing."""
        return IncidentRecord(
            status=IncidentStatus.RESOLVED,
            **kwargs,
        )

    def _verified(self, outcome: VerificationOutcome) -> VerificationResult:
        return VerificationResult(outcome=outcome)

    def _judge(self, score: float) -> SemanticReward:
        return SemanticReward(
            logical_consistency=score,
            action_relevance=score,
            expert_accuracy=score,
            overall_quality_score=score,
            justification="test",
        )

    def test_verified_recovery_positive(self) -> None:
        incident = self._make_incident(verification_result=self._verified(VerificationOutcome.VERIFIED_RECOVERY))
        assert compute_reward(incident) == pytest.approx(0.6)

    def test_not_recovered_negative(self) -> None:
        incident = self._make_incident(verification_result=self._verified(VerificationOutcome.NOT_RECOVERED))
        assert compute_reward(incident) == pytest.approx(-0.6)

    def test_inconclusive_is_no_signal(self) -> None:
        incident = self._make_incident(verification_result=self._verified(VerificationOutcome.INCONCLUSIVE))
        assert compute_reward(incident) == 0.0

    def test_regression_penalized(self) -> None:
        verified = self._verified(VerificationOutcome.VERIFIED_RECOVERY)
        stable = compute_reward(self._make_incident(verification_result=verified, regressed=False))
        regressed = compute_reward(self._make_incident(verification_result=verified, regressed=True))
        assert stable > 0.6 > regressed

    def test_speed_does_not_change_reward(self) -> None:
        verified = self._verified(VerificationOutcome.VERIFIED_RECOVERY)
        fast = self._make_incident(verification_result=verified, time_to_mitigate_seconds=30.0)
        slow = self._make_incident(verification_result=verified, time_to_mitigate_seconds=9000.0)
        assert compute_reward(fast) == compute_reward(slow)

    def test_unverified_auto_resolve_not_rewarded(self) -> None:
        assert compute_reward(self._make_incident(auto_resolved=True, time_to_mitigate_seconds=60.0)) == 0.0

    def test_missing_judge_uses_extrinsic_only(self) -> None:
        incident = self._make_incident(verification_result=self._verified(VerificationOutcome.VERIFIED_RECOVERY))
        assert compute_reward(incident, None) == pytest.approx(0.6)

    def test_judge_blends_with_extrinsic(self) -> None:
        incident = self._make_incident(verification_result=self._verified(VerificationOutcome.VERIFIED_RECOVERY))
        # 0.6*0.6 + 0.4*(1.0*2-1)
        assert compute_reward(incident, self._judge(1.0)) == pytest.approx(0.76)

    def test_false_positive_penalty(self) -> None:
        """False positive should give negative reward."""
        incident = self._make_incident(
            false_positive=True,
        )
        reward = compute_reward(incident)
        assert reward < 0

    def test_human_override_penalty(self) -> None:
        """Human override should penalize the agent."""
        incident = self._make_incident(
            human_overrode=True,
        )
        reward = compute_reward(incident)
        assert reward < 0

    def test_combined_bad_outcome(self) -> None:
        """False positive + human override = maximum penalty."""
        incident = self._make_incident(
            false_positive=True,
            human_overrode=True,
        )
        reward = compute_reward(incident)
        assert reward <= -0.5

    def test_no_resolution_data(self) -> None:
        """Incident with no resolution data should give zero reward."""
        incident = self._make_incident()
        reward = compute_reward(incident)
        assert reward == 0.0
