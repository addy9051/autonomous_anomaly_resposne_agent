"""
Multi-Tenant Domain Schemas for Autonomous SRE SaaS.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(str, Enum):
    INVESTIGATING = "investigating"
    RECOMMENDATION_READY = "recommendation_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class ActionTier(int, Enum):
    TIER_1_AUTO = 1      # Safe, reversible, allowlisted (auto-executed if enabled)
    TIER_2_APPROVAL = 2  # Standard remediation, requires human approval click
    TIER_3_ESCALATE = 3  # Critical or high-blast radius, human execution only


class VerifierOutcome(str, Enum):
    RECOVERED = "recovered"
    NOT_RECOVERED = "not_recovered"
    INCONCLUSIVE = "inconclusive"


class TenantContext(BaseModel):
    """Authenticated context for the current API request."""
    tenant_id: str
    user_id: str = "system"
    role: str = "sre"
    key_name: str | None = None


class AnomalyAlert(BaseModel):
    """Inbound telemetry alert from monitoring systems."""
    title: str
    service: str
    anomaly_type: str = "performance_degradation"
    severity: Severity = Severity.MEDIUM
    summary: str = ""
    metrics: dict[str, float] = Field(default_factory=dict)
    labels: dict[str, str] = Field(default_factory=dict)
    source: str = "webhook"  # alertmanager, datadog, prometheus, custom


class EvidenceRecord(BaseModel):
    id: str  # E1, E2, E3...
    source: str
    timestamp: str
    content: str
    is_anomaly: bool = False


class Hypothesis(BaseModel):
    hypothesis_id: str
    title: str
    root_cause_explanation: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    recommended_action: str
    action_type: str = "restart_unhealthy_pod"
    action_tier: ActionTier = ActionTier.TIER_2_APPROVAL
    parameters: dict[str, Any] = Field(default_factory=dict)


class ActionProposal(BaseModel):
    id: str | None = None
    incident_id: str
    tenant_id: str
    tier: ActionTier
    action_type: str
    target_service: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    proposal_hash: str
    status: str = "pending"
    approved_by: str | None = None
    approval_reason: str | None = None
    execution_result: dict[str, Any] | None = None
    verifier_outcome: VerifierOutcome | None = None
    created_at: datetime | None = None


class SREBrief(BaseModel):
    incident_id: str
    tenant_id: str
    title: str
    severity: Severity
    service: str
    root_cause: str
    confidence: float
    fix_applied_or_recommended: str
    evidence_citations: list[str] = Field(default_factory=list)
    action_tier: ActionTier
    status: IncidentStatus
    timeline_summary: list[str] = Field(default_factory=list)


class IncidentRecord(BaseModel):
    incident_id: str
    tenant_id: str
    title: str
    status: IncidentStatus = IncidentStatus.INVESTIGATING
    severity: Severity = Severity.MEDIUM
    service: str
    fingerprint: str
    root_cause: str | None = None
    confidence: float = 0.0
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence_summary: list[EvidenceRecord] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
