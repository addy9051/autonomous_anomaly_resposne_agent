"""
Deterministic Action Policy Gate.

Enforces deterministic authorization, parameter boundary checking,
and environment gating before any action can be executed or proposed.
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field

from shared.config import get_settings
from shared.schemas import ActionPolicyDecision, ActionTier
from shared.utils import get_logger

logger = get_logger("action_policy")


class PolicyEvaluationResult(BaseModel):
    """Result of deterministic policy evaluation."""

    decision: ActionPolicyDecision
    reason: str
    requires_approval: bool
    validated_params: dict[str, Any] = Field(default_factory=dict)
    is_simulated: bool = False
    verification_criteria: dict[str, Any] = Field(default_factory=dict)


class DeterministicActionPolicy:
    """
    Deterministic safety policy gate for remediation actions.
    
    Principles:
    1. Default stance: Fail closed.
    2. Production actions are RECOMMEND-ONLY or require SRE APPROVAL. No autonomous writes in production.
    3. Disables development auto-approval when touching real infrastructure (dry_run=False).
    4. Validates parameters within strict, typed bounds.
    """

    # Actions considered safe for bounded automation ONLY in non-production with dry_run
    LOW_RISK_ACTIONS = {
        "scale_replicas",
        "clear_cache",
        "restart_unhealthy_pod",
        "update_circuit_breaker",
        "increase_timeout",
        "add_rate_limit",
        "flush_redis_key",
        "rotate_log_level",
    }

    # Medium risk actions that require explicit human approval in all environments
    MEDIUM_RISK_ACTIONS = {
        "drain_node",
        "failover_db",
        "throttle_merchant",
        "kill_long_running_queries",
        "increase_connection_pool",
        "modify_autoscaling_policy",
    }

    # High risk / human-only actions that must be recommend-only
    HIGH_RISK_ACTIONS = {
        "rollback_deployment",
        "block_issuer",
        "network_policy_change",
        "database_schema_migration",
        "disable_service",
        "modify_firewall_rules",
    }

    @classmethod
    def evaluate(
        cls,
        action_name: str,
        service: str,
        environment: str,
        params: dict[str, Any],
        dry_run: bool = True,
        target: str | None = None,
    ) -> PolicyEvaluationResult:
        """
        Evaluate an action against deterministic policy rules.
        """
        settings = get_settings()
        env = (environment or settings.app.app_env or "production").lower()

        # 1. Parameter boundary validation
        param_error = cls._validate_params(action_name, params)
        if param_error:
            logger.warning("action_policy_denied_params", action=action_name, reason=param_error)
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.DENIED,
                reason=f"Parameter validation failed: {param_error}",
                requires_approval=True,
                validated_params=params,
                is_simulated=dry_run,
            )

        # 2. High risk actions are ALWAYS Recommend-Only
        if action_name in cls.HIGH_RISK_ACTIONS:
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.RECOMMEND_ONLY,
                reason="High-blast action: restricted to human-on-call manual execution with runbook guidance.",
                requires_approval=True,
                validated_params=params,
                is_simulated=dry_run,
                verification_criteria={"health_check": True, "error_rate_threshold": 0.01},
            )

        # 3. Production Environment Stance: Recommend-Only or Approval-Required
        if env == "production":
            # In production, NO autonomous writes are allowed by default
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                reason="Production environment: deterministic policy mandates explicit SRE approval before execution.",
                requires_approval=True,
                validated_params=params,
                is_simulated=dry_run,
                verification_criteria={"health_check": True, "error_rate_threshold": 0.01},
            )

        # 4. Non-production (development / staging / test)
        # If real execution requested (dry_run=False), do NOT auto-approve if it can reach infrastructure
        if not dry_run:
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                reason="Live infrastructure execution in non-prod requires explicit approval to prevent unintentional mutations.",
                requires_approval=True,
                validated_params=params,
                is_simulated=False,
                verification_criteria={"health_check": True},
            )

        # 5. In simulation / dry_run mode for low-risk actions
        if action_name in cls.LOW_RISK_ACTIONS:
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.PERMITTED_AUTO,
                reason="Permitted for bounded automated simulation under dry-run constraints.",
                requires_approval=False,
                validated_params=params,
                is_simulated=True,
                verification_criteria={"health_check": True, "error_rate_threshold": 0.02},
            )

        # Medium risk actions in dev dry-run
        if action_name in cls.MEDIUM_RISK_ACTIONS:
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                reason="Medium-risk action requires approval even in development simulation.",
                requires_approval=True,
                validated_params=params,
                is_simulated=True,
                verification_criteria={"health_check": True},
            )

        # Unknown action -> Default fail closed
        return PolicyEvaluationResult(
            decision=ActionPolicyDecision.DENIED,
            reason=f"Action '{action_name}' is not in the allowlisted catalog. Action denied by policy.",
            requires_approval=True,
            validated_params=params,
            is_simulated=dry_run,
        )

    @classmethod
    def _validate_params(cls, action_name: str, params: dict[str, Any]) -> str | None:
        """Check action parameter boundaries."""
        if action_name == "scale_replicas":
            replicas = params.get("replicas")
            if replicas is not None:
                if not isinstance(replicas, int) or replicas < 1 or replicas > 10:
                    return f"replicas must be an integer between 1 and 10 (received {replicas})"

        elif action_name == "increase_timeout":
            timeout_ms = params.get("timeout_ms")
            if timeout_ms is not None:
                if not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0 or timeout_ms > 30000:
                    return f"timeout_ms must be positive and <= 30000ms (received {timeout_ms})"

        elif action_name == "update_circuit_breaker":
            thresh = params.get("failure_rate_threshold")
            if thresh is not None:
                if not isinstance(thresh, (int, float)) or thresh < 0.01 or thresh > 0.90:
                    return f"failure_rate_threshold must be between 0.01 and 0.90 (received {thresh})"

        elif action_name == "kill_long_running_queries":
            thresh_sec = params.get("threshold_seconds")
            if thresh_sec is not None:
                if not isinstance(thresh_sec, (int, float)) or thresh_sec < 5:
                    return f"threshold_seconds must be at least 5s (received {thresh_sec})"

        return None
