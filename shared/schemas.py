"""
Pydantic v2 schemas — inter-agent API contracts.

Every message exchanged between agents is validated against these models.
If LLM output fails validation, the agent retries up to 3 times with a
"fix the JSON" prompt before escalating with raw output to human review.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─── Enums ────────────────────────────────────────────────────────


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AnomalyType(str, Enum):
    LATENCY_SPIKE = "latency_spike"
    ERROR_RATE = "error_rate"
    DATA_QUALITY = "data_quality"
    FRAUD_SIGNAL = "fraud_signal"
    VOLUME_ANOMALY = "volume_anomaly"
    RESOURCE_SATURATION = "resource_saturation"


class RootCauseCategory(str, Enum):
    NETWORK = "network"
    DATABASE = "database"
    APPLICATION = "application"
    EXTERNAL = "external"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


class ActionTier(int, Enum):
    """Tier 1 = autonomous, Tier 2 = needs SRE approval, Tier 3 = always human."""
    TIER_1_AUTO = 1
    TIER_2_APPROVE = 2
    TIER_3_HUMAN = 3


class IncidentStatus(str, Enum):
    DETECTED = "detected"
    DIAGNOSING = "diagnosing"
    ACTION_PENDING = "action_pending"
    ACTION_EXECUTING = "action_executing"
    AWAITING_APPROVAL = "awaiting_approval"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    FALSE_POSITIVE = "false_positive"


# ─── Monitoring Agent → Diagnosis Agent ──────────────────────────


class MetricsSnapshot(BaseModel):
    """Point-in-time metrics for the affected service."""
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    p99_latency_ms: float | None = None
    error_rate: float | None = None
    request_rate: float | None = None
    cpu_percent: float | None = None
    memory_percent: float | None = None
    kafka_consumer_lag: int | None = None
    fraud_score_mean: float | None = None
    custom_metrics: dict[str, float] = Field(default_factory=dict)


class AnomalyEvent(BaseModel):
    """
    Output of the Monitoring Agent.
    Sent to the Diagnosis Agent when an anomaly is detected.
    """
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    severity: Severity
    affected_services: list[str]
    anomaly_type: AnomalyType
    metrics_snapshot: MetricsSnapshot
    reasoning: str = Field(
        ...,
        description="LLM reasoning chain explaining why this was flagged as anomalous",
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0,
        description="Anomaly confidence score (0–1). Escalate if > 0.75",
    )
    raw_event: dict[str, Any] = Field(
        default_factory=dict,
        description="Original telemetry event that triggered the anomaly",
    )


# ─── Diagnosis Agent → Action Agent ─────────────────────────────


class RunbookReference(BaseModel):
    """Reference to a matched runbook in the knowledge base."""
    runbook_id: str
    title: str
    similarity_score: float
    relevant_steps: list[str] = Field(default_factory=list)


class SubAgentReport(BaseModel):
    """Report from a specialist sub-agent (Network/DB/App)."""
    agent_type: str
    findings: str
    severity: Severity
    evidence: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)


class RecommendedAction(BaseModel):
    """An action recommended by the Diagnosis Agent."""
    action: str
    tier: ActionTier
    params: dict[str, Any] = Field(default_factory=dict)
    estimated_impact: str = ""
    rollback_steps: list[str] = Field(default_factory=list)


class DiagnosisResult(BaseModel):
    """
    Output of the Diagnosis Agent.
    Sent to the Action Agent with root cause analysis and recommended actions.
    """
    incident_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_id: str = Field(description="Link back to the triggering AnomalyEvent")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    root_cause: str
    root_cause_category: RootCauseCategory
    runbook_references: list[RunbookReference] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction]
    sub_agent_reports: dict[str, SubAgentReport] = Field(default_factory=dict)
    reasoning_chain: str = Field(
        ...,
        description="Full chain-of-thought reasoning for the diagnosis",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    is_novel_incident: bool = Field(
        default=False,
        description="True if no runbook matched with similarity >= 0.75",
    )


# ─── Action Agent → Feedback Loop ───────────────────────────────


class ActionResult(BaseModel):
    """
    Output of the Action Agent.
    Records what action was taken and its outcome.
    """
    incident_id: str
    action_taken: str
    tier: ActionTier
    execution_status: str  # "success", "failed", "skipped", "pending_approval"
    execution_time_ms: float = 0.0
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    human_approved: bool | None = None
    slack_thread_ts: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ─── Full Incident Record ───────────────────────────────────────


class IncidentRecord(BaseModel):
    """
    Complete lifecycle record for an incident.
    Stored in PostgreSQL/Spanner for durability.
    """
    incident_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: IncidentStatus = IncidentStatus.DETECTED
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Phase outputs
    anomaly_event: AnomalyEvent | None = None
    diagnosis_result: DiagnosisResult | None = None
    action_results: list[ActionResult] = Field(default_factory=list)

    # Resolution metadata
    resolved_at: datetime | None = None
    time_to_detect_seconds: float | None = None
    time_to_mitigate_seconds: float | None = None
    auto_resolved: bool = False
    false_positive: bool = False
    human_overrode: bool = False
    human_feedback: str | None = None

    # Cost tracking
    total_llm_tokens_used: int = 0
    total_llm_cost_usd: float = 0.0


# ─── Telemetry Event (raw input) ─────────────────────────────────


class TelemetryEvent(BaseModel):
    """Raw telemetry event ingested from Kafka or the in-memory event bus."""
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source: str  # "payment_gateway", "fraud_api", "infra_metrics", etc.
    service_name: str
    event_type: str  # "transaction", "metric", "trace", "alert"
    payload: dict[str, Any] = Field(default_factory=dict)
