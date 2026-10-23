"""
Deterministic Action Policy Gate & Safety Boundaries.
Never relies on the LLM to decide safety: critical alerts always fail safe to human triage.
"""

from __future__ import annotations

import json
from typing import Any

from core.config import get_settings
from core.schemas import ActionProposal, ActionTier, Hypothesis, Severity
from core.security import compute_action_proposal_hash
from core.utils import get_logger
from database.connection import execute, fetch_all, fetch_one

logger = get_logger("policy_gate")


class DeterministicPolicyGate:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.settings = get_settings()

    async def evaluate_action(
        self,
        incident_id: str,
        service: str,
        severity: Severity,
        hypothesis: Hypothesis,
    ) -> ActionProposal:
        """
        Evaluate proposed action against deterministic safety constraints:
        1. Critical incidents: RCA-only, escalated to human (Tier 3).
        2. Non-allowlisted actions: Tier 2 approval required.
        3. Hourly execution rate limits.
        4. Hash binding to prevent parameter tampering.
        """
        action_type = hypothesis.action_type
        parameters = hypothesis.parameters
        parameters.setdefault("service", service)

        # 1. Critical Severity Rule: Always escalate to human SRE
        if severity == Severity.CRITICAL:
            effective_tier = ActionTier.TIER_3_ESCALATE
            status = "escalated"
        elif not self.settings.auto_remediation_enabled:
            # If auto-remediation is disabled globally, enforce Tier 2 approval
            effective_tier = ActionTier.TIER_2_APPROVAL
            status = "pending"
        elif action_type not in self.settings.auto_action_allowlist.split(","):
            # If action not on strict allowlist, require approval
            effective_tier = ActionTier.TIER_2_APPROVAL
            status = "pending"
        else:
            # Check hourly execution rate limit for this service
            recent_count = await self._get_recent_action_count(service)
            if recent_count >= self.settings.auto_action_max_per_hour:
                logger.warning("hourly_rate_limit_exceeded", service=service, count=recent_count)
                effective_tier = ActionTier.TIER_2_APPROVAL
                status = "pending"
            else:
                effective_tier = ActionTier.TIER_1_AUTO
                status = "approved"

        # Compute tamper-proof proposal hash
        prop_hash = compute_action_proposal_hash(incident_id, action_type, service, parameters)

        # Store in PostgreSQL
        query = """
            INSERT INTO action_proposals (
                incident_id, tenant_id, tier, action_type, target_service,
                parameters, proposal_hash, status, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6::jsonb, $7, $8, NOW(), NOW()
            ) RETURNING id
        """
        row = await fetch_one(
            query,
            incident_id,
            self.tenant_id,
            effective_tier.value,
            action_type,
            service,
            json.dumps(parameters),
            prop_hash,
            status,
        )

        proposal_id = str(row["id"]) if row else "generated"
        return ActionProposal(
            id=proposal_id,
            incident_id=incident_id,
            tenant_id=self.tenant_id,
            tier=effective_tier,
            action_type=action_type,
            target_service=service,
            parameters=parameters,
            proposal_hash=prop_hash,
            status=status,
        )

    async def _get_recent_action_count(self, service: str) -> int:
        """Count actions executed for this service in the last 60 minutes."""
        query = """
            SELECT COUNT(*) AS cnt FROM action_proposals
            WHERE tenant_id = $1
              AND target_service = $2
              AND status IN ('executing', 'completed')
              AND created_at > NOW() - INTERVAL '1 hour'
        """
        row = await fetch_one(query, self.tenant_id, service)
        return int(row["cnt"]) if row else 0
