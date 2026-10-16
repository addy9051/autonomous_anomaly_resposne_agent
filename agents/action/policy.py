"""
Deterministic Action Policy Gate.

Enforces deterministic authorization, parameter boundary checking,
and environment gating before any action can be executed or proposed.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict, deque
from typing import Any

from pydantic import BaseModel, Field

from agents.action.workflows import N8N_WORKFLOWS
from shared.config import get_settings
from shared.schemas import ActionPolicyDecision
from shared.utils import get_logger

logger = get_logger("action_policy")


def compute_proposal_hash(
    action_name: str,
    params: dict[str, Any],
    *,
    service: str | None = None,
    environment: str | None = None,
    dry_run: bool = False,
    policy_decision: str | None = None,
    verification_criteria: dict[str, Any] | None = None,
) -> str:
    """Digest every execution-relevant field so approval cannot be altered in transit or storage."""
    canonical = json.dumps(
        {
            "action": action_name,
            "params": params,
            "service": service or "",
            "environment": environment or "",
            "dry_run": dry_run,
            "policy_decision": policy_decision,
            "verification_criteria": verification_criteria or {},
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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

    # Actions considered reversible; the configurable auto allowlist can only narrow this set
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

    _auto_history: dict[str, deque[float]] = defaultdict(deque)

    @classmethod
    def _auto_allowed(cls, action_name: str, service: str, dry_run: bool, env: str) -> tuple[bool, str]:
        """Whether an allowlisted reversible action may run without approval, with the reason if not."""
        agent_cfg = get_settings().agent
        allowlist = {a.strip() for a in agent_cfg.auto_action_allowlist.split(",") if a.strip()}
        allowlist &= cls.LOW_RISK_ACTIONS  # the allowlist can only narrow the reversible set
        if action_name not in allowlist:
            return False, "action is not in the auto-remediation allowlist"
        if dry_run and env != "production":
            return True, "simulation"
        if not agent_cfg.auto_remediation_enabled:
            return False, "auto-remediation is disabled"
        targets = {t.strip() for t in agent_cfg.auto_action_target_services.split(",") if t.strip()}
        if "*" not in targets and service not in targets:
            return False, f"service '{service}' is not in the auto-remediation target list"
        now = time.time()
        recent = cls._auto_history[service]
        while recent and now - recent[0] > 3600:
            recent.popleft()
        if len(recent) >= agent_cfg.auto_action_max_per_hour:
            return False, "hourly auto-remediation limit reached for this service"
        return True, "allowlisted"

    @classmethod
    def record_auto_execution(cls, service: str) -> None:
        cls._auto_history[service].append(time.time())

    @classmethod
    def evaluate(
        cls,
        action_name: str,
        service: str,
        environment: str,
        params: dict[str, Any],
        dry_run: bool = True,
        target: str | None = None,
        severity: str | None = None,
    ) -> PolicyEvaluationResult:
        """
        Evaluate an action against deterministic policy rules.
        """
        settings = get_settings()
        env = (environment or settings.app.app_env or "production").lower()

        # 0. Critical incidents are root-cause-only: no action of any kind.
        if (severity or "").lower() == "critical":
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.DENIED,
                reason="Critical incident: root-cause analysis only, no remediation is proposed or executed.",
                requires_approval=True,
                validated_params=params,
                is_simulated=dry_run,
            )

        # 1. Only actions with a registered workflow can reach execution.
        if action_name not in N8N_WORKFLOWS and action_name not in cls.HIGH_RISK_ACTIONS:
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.DENIED,
                reason=f"Action '{action_name}' has no registered workflow.",
                requires_approval=True,
                validated_params=params,
                is_simulated=dry_run,
            )

        # 2. Parameter boundary validation
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

        # 3. Allowlisted reversible actions on non-critical incidents may run without approval
        auto_ok, auto_reason = cls._auto_allowed(action_name, service, dry_run, env)
        if auto_ok:
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.PERMITTED_AUTO,
                reason=f"Permitted for automated execution ({auto_reason}); reversible, non-critical.",
                requires_approval=False,
                validated_params=params,
                is_simulated=dry_run,
                verification_criteria={"health_check": True, "error_rate_threshold": 0.02},
            )

        # 4. Everything else needs explicit SRE approval in production
        if env == "production":
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                reason=f"Explicit SRE approval required before execution ({auto_reason}).",
                requires_approval=True,
                validated_params=params,
                is_simulated=dry_run,
                verification_criteria={"health_check": True, "error_rate_threshold": 0.01},
            )

        # 5. Non-production: real execution of anything not auto-allowed needs approval
        if not dry_run:
            return PolicyEvaluationResult(
                decision=ActionPolicyDecision.APPROVAL_REQUIRED,
                reason=(
                    "Live infrastructure execution in non-prod requires explicit approval "
                    "to prevent unintentional mutations."
                ),
                requires_approval=True,
                validated_params=params,
                is_simulated=False,
                verification_criteria={"health_check": True},
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

    # The workflow registry is the source of truth for execution parameters.
    REQUIRED_PARAMS: dict[str, list[str]] = {
        name: list(definition["params"])
        for name, definition in N8N_WORKFLOWS.items()
    }

    @classmethod
    def _validate_params(cls, action_name: str, params: dict[str, Any]) -> str | None:
        """Check action parameter presence, names, and boundaries."""
        # 1. Required parameter presence check (aligned with workflow registry)
        required = cls.REQUIRED_PARAMS.get(action_name)
        if required:
            missing = [p for p in required if p not in params or params[p] is None]
            if missing:
                return f"Missing required parameters for '{action_name}': {missing}"

        # 2. Per-action boundary validation
        if action_name == "scale_replicas":
            replicas = params.get("replicas")
            if replicas is not None:
                if not isinstance(replicas, int) or replicas < 1 or replicas > 10:
                    return f"replicas must be an integer between 1 and 10 (received {replicas})"

        elif action_name == "increase_timeout":
            timeout_ms = params.get("new_timeout_ms")
            if timeout_ms is not None:
                if not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0 or timeout_ms > 30000:
                    return f"new_timeout_ms must be positive and <= 30000ms (received {timeout_ms})"

        elif action_name == "update_circuit_breaker":
            thresh = params.get("threshold")
            if thresh is not None:
                if not isinstance(thresh, (int, float)) or thresh < 0.01 or thresh > 0.90:
                    return f"threshold must be between 0.01 and 0.90 (received {thresh})"
            timeout_ms = params.get("timeout_ms")
            if timeout_ms is not None:
                if not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0 or timeout_ms > 30000:
                    return f"timeout_ms must be positive and <= 30000ms (received {timeout_ms})"

        elif action_name == "kill_long_running_queries":
            thresh_sec = params.get("threshold_seconds")
            if thresh_sec is not None:
                if not isinstance(thresh_sec, (int, float)) or thresh_sec < 5:
                    return f"threshold_seconds must be at least 5s (received {thresh_sec})"

        elif action_name == "throttle_merchant":
            rps = params.get("rate_limit_rps")
            if rps is not None:
                if not isinstance(rps, (int, float)) or rps <= 0 or rps > 10000:
                    return f"rate_limit_rps must be between 1 and 10000 (received {rps})"

        elif action_name == "increase_connection_pool":
            max_conn = params.get("new_max_connections")
            if max_conn is not None:
                if not isinstance(max_conn, int) or max_conn < 1 or max_conn > 500:
                    return f"new_max_connections must be an integer between 1 and 500 (received {max_conn})"

        elif action_name == "add_rate_limit":
            rps = params.get("rate_limit_rps")
            if rps is not None:
                if not isinstance(rps, (int, float)) or rps <= 0 or rps > 100000:
                    return f"rate_limit_rps must be between 1 and 100000 (received {rps})"

        return None
