"""
Unit tests for the reward function and action tiers.
"""

from __future__ import annotations

from typing import Any

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

    def test_auto_resolved_fast(self) -> None:
        """Fast auto-resolution should give positive reward."""
        incident = self._make_incident(
            auto_resolved=True,
            time_to_mitigate_seconds=300.0,  # 5 min (baseline 45 min)
        )
        reward = compute_reward(incident)
        assert reward > 0.5  # Significant positive reward

    def test_auto_resolved_slow(self) -> None:
        """Slow auto-resolution should give smaller reward."""
        incident = self._make_incident(
            auto_resolved=True,
            time_to_mitigate_seconds=2400.0,  # 40 min (close to baseline)
        )
        reward = compute_reward(incident)
        assert reward > 0  # Still positive but smaller

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

    def test_perfect_outcome(self) -> None:
        """Auto-resolved very fast + correct detection = high reward."""
        incident = self._make_incident(
            auto_resolved=True,
            time_to_mitigate_seconds=60.0,  # 1 min
            time_to_detect_seconds=30.0,  # 30 sec
        )
        reward = compute_reward(incident)
        assert reward > 0.7  # High reward

    def test_no_resolution_data(self) -> None:
        """Incident with no resolution data should give zero reward."""
        incident = self._make_incident()
        reward = compute_reward(incident)
        assert reward == 0.0
