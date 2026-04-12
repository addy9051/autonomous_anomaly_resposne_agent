"""
Action Tier Classification.

Actions are classified into 3 tiers by blast radius:
- Tier 1: Autonomous — safe actions executed without human approval
- Tier 2: Approval Required — needs async SRE approval via Slack
- Tier 3: Human Only — always pages on-call, never autonomous
"""

from __future__ import annotations

from shared.schemas import ActionTier

# ─── Tier Definitions ────────────────────────────────────────────

ACTION_TIERS: dict[str, ActionTier] = {
    # ═══ Tier 1 — Autonomous (low blast radius) ═══
    "scale_replicas": ActionTier.TIER_1_AUTO,
    "clear_cache": ActionTier.TIER_1_AUTO,
    "restart_unhealthy_pod": ActionTier.TIER_1_AUTO,
    "update_circuit_breaker": ActionTier.TIER_1_AUTO,
    "increase_timeout": ActionTier.TIER_1_AUTO,
    "add_rate_limit": ActionTier.TIER_1_AUTO,
    "flush_redis_key": ActionTier.TIER_1_AUTO,
    "rotate_log_level": ActionTier.TIER_1_AUTO,
    # ═══ Tier 2 — Approval Required (medium blast radius) ═══
    "drain_node": ActionTier.TIER_2_APPROVE,
    "failover_db": ActionTier.TIER_2_APPROVE,
    "throttle_merchant": ActionTier.TIER_2_APPROVE,
    "kill_long_running_queries": ActionTier.TIER_2_APPROVE,
    "increase_connection_pool": ActionTier.TIER_2_APPROVE,
    "modify_autoscaling_policy": ActionTier.TIER_2_APPROVE,
    # ═══ Tier 3 — Human Only (high blast radius) ═══
    "rollback_deployment": ActionTier.TIER_3_HUMAN,
    "block_issuer": ActionTier.TIER_3_HUMAN,
    "network_policy_change": ActionTier.TIER_3_HUMAN,
    "database_schema_migration": ActionTier.TIER_3_HUMAN,
    "disable_service": ActionTier.TIER_3_HUMAN,
    "modify_firewall_rules": ActionTier.TIER_3_HUMAN,
}


def classify_action(action_name: str) -> ActionTier:
    """
    Classify an action into a tier.
    Unknown actions default to Tier 3 (human required).
    """
    return ACTION_TIERS.get(action_name, ActionTier.TIER_3_HUMAN)


def get_tier_description(tier: ActionTier) -> str:
    """Human-readable description of a tier."""
    descriptions = {
        ActionTier.TIER_1_AUTO: "🟢 Autonomous — executes immediately without approval",
        ActionTier.TIER_2_APPROVE: "🟡 Approval Required — needs SRE confirmation via Slack",
        ActionTier.TIER_3_HUMAN: "🔴 Human Only — pages on-call engineer for manual execution",
    }
    return descriptions.get(tier, "Unknown tier")


def get_tier_actions(tier: ActionTier) -> list[str]:
    """Get all actions for a specific tier."""
    return [action for action, t in ACTION_TIERS.items() if t == tier]
