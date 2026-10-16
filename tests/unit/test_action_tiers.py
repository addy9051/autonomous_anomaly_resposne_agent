"""Unit tests for action tier classification."""

from __future__ import annotations

from agents.action.tiers import (
    ACTION_TIERS,
    classify_action,
    get_tier_actions,
    get_tier_description,
)
from shared.schemas import ActionTier


class TestActionTiers:
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
        assert classify_action("unknown_dangerous_action") == ActionTier.TIER_3_HUMAN
        assert classify_action("delete_production_database") == ActionTier.TIER_3_HUMAN

    def test_all_actions_have_tiers(self) -> None:
        for action, tier in ACTION_TIERS.items():
            assert tier in ActionTier
            assert isinstance(action, str)

    def test_tier_descriptions(self) -> None:
        assert "Autonomous" in get_tier_description(ActionTier.TIER_1_AUTO)
        assert "Approval" in get_tier_description(ActionTier.TIER_2_APPROVE)
        assert "Human" in get_tier_description(ActionTier.TIER_3_HUMAN)
