"""
Pydantic v2 schemas — inter-agent API contracts.

Every message exchanged between agents is validated against these models.
If LLM output fails validation, the agent retries up to 3 times with a
"fix the JSON" prompt before escalating with raw output to human review.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

# ─── Enums ────────────────────────────────────────────────────────


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class AnomalyType(StrEnum):
    LATENCY_SPIKE = "latency_spike"
    ERROR_RATE = "error_rate"
    DATA_QUALITY = "data_quality"
    FRAUD_SIGNAL = "fraud_signal"
    VOLUME_ANOMALY = "volume_anomaly"
    RESOURCE_SATURATION = "resource_saturation"
    SECURITY = "security"


class RootCauseCategory(StrEnum):
    NETWORK = "network"
    DATABASE = "database"
    APPLICATION = "application"
    EXTERNAL = "external"
    INFRASTRUCTURE = "infrastructure"
    SECURITY = "security"
    UNKNOWN = "unknown"


class TelemetrySignalType(StrEnum):
    METRIC = "metric"
    LOG_SUMMARY = "log_summary"
    TRACE = "trace"
    SYNTHETIC_PROBE = "synthetic_probe"
    BUSINESS_SIGNAL = "business_signal"
    DEPLOY_EVENT = "deploy_event"


class TelemetryConnectorSource(StrEnum):
    SIMULATOR = "simulator"
    DATADOG = "datadog"
    SPLUNK = "splunk"
    PROMETHEUS = "prometheus"
    SNOWFLAKE = "snowflake"
    GITHUB = "github"
    CUSTOM = "custom"


class ActionTier(int, Enum):
    """Tier 1 = autonomous, Tier 2 = needs SRE approval, Tier 3 = always human."""

    TIER_1_AUTO = 1
    TIER_2_APPROVE = 2
    TIER_3_HUMAN = 3


class ActionPolicyDecision(StrEnum):
    PERMITTED_AUTO = "permitted_auto"
    APPROVAL_REQUIRED = "approval_required"
    RECOMMEND_ONLY = "recommend_only"
    DENIED = "denied"


class VerificationOutcome(StrEnum):
    VERIFIED_RECOVERY = "verified_recovery"
    NOT_RECOVERED = "not_recovered"
    INCONCLUSIVE = "inconclusive"


class IncidentStatus(StrEnum):
    DETECTED = "detected"
    INVESTIGATING = "investigating"
    DIAGNOSING = "diagnosing"  # backwards compatibility alias for investigating
    RECOMMENDATION_READY = "recommendation_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    ACTION_PENDING = "action_pending"  # backwards compatibility alias for awaiting_approval
    EXECUTING = "executing"
    ACTION_EXECUTING = "action_executing"  # backwards compatibility alias
    VERIFYING = "verifying"
    VERIFIED_RECOVERED = "verified_recovered"
    RESOLVED = "resolved"  # backwards compatibility alias for verified_recovered
    ESCALATED = "escalated"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    severity: Severity
    affected_services: list[str]
    anomaly_type: AnomalyType
    metrics_snapshot: MetricsSnapshot
    reasoning: str = Field(
        ...,
        description="LLM reasoning chain explaining why this was flagged as anomalous",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Anomaly confidence score (0–1). Escalate if > 0.75",
    )
    raw_event: dict[str, Any] = Field(
        default_factory=dict,
        description="Original telemetry event that triggered the anomaly",
    )


# ─── Evidence & Hypothesis Models ────────────────────────────────


class EvidenceRecord(BaseModel):
    """A single piece of time-aligned, source-linked evidence with provenance."""

    source: str = Field(..., description="Source system (e.g. prometheus, datadog, k8s, logs)")
    query_or_ref: str = Field(..., description="Query, metric name, or reference identifier")
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_time: datetime | None = None
    freshness_seconds: float | None = None
    metric_name: str | None = None
    value: Any = None
    unit: str = ""
    is_contradictory: bool = False
    description: str = ""
    tags: dict[str, Any] = Field(default_factory=dict)


class Hypothesis(BaseModel):
    """A candidate explanation for the incident with supporting and contradicting observations."""

    hypothesis: str = Field(..., description="Concise statement of candidate cause")
    affected_scope: list[str] = Field(default_factory=list, description="Affected services or resources")
    supporting_evidence: list[EvidenceRecord] = Field(default_factory=list)
    contradicting_evidence: list[EvidenceRecord] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    confidence_band: str = Field(default="medium", description="high, medium, low, or uncalibrated")
    next_safe_query: str = Field(default="", description="Next safe query or human question to test this hypothesis")


class VerificationResult(BaseModel):
    """Independent verification report evaluating post-action service recovery."""

    outcome: VerificationOutcome
    checked_signals: dict[str, Any] = Field(default_factory=dict)
    details: str = ""
    stabilization_seconds: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    root_cause: str
    root_cause_category: RootCauseCategory
    decision_summary: str = Field(
        default="",
        description=(
            "Responder-facing decision summary: conclusion, evidence references, assumptions, alternatives, next step"
        ),
    )
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence_timeline: list[EvidenceRecord] = Field(default_factory=list)
    missing_sources: list[str] = Field(default_factory=list)
    runbook_references: list[RunbookReference] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction]
    sub_agent_reports: dict[str, SubAgentReport] = Field(default_factory=dict)
    reasoning_chain: str = Field(
        default="",
        description="Deprecated internal chain-of-thought, decision_summary preferred for responder display",
    )
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_band: str = Field(default="medium", description="high, medium, low, uncalibrated")
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
    execution_status: (
        str  # "success", "failed", "skipped", "pending_approval", "rejected", "policy_denied", "recommend_only"
    )
    execution_time_ms: float = 0.0
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    human_approved: bool | None = None
    approver: str | None = None
    policy_decision: ActionPolicyDecision | None = None
    is_simulated: bool = False
    proposal_hash: str | None = None
    verification_criteria: dict[str, Any] = Field(default_factory=dict)
    proposed_service: str | None = None
    proposed_environment: str | None = None
    slack_thread_ts: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ─── Full Incident Record ───────────────────────────────────────


class IncidentRecord(BaseModel):
    """
    Complete lifecycle record for an incident.
    Stored in persistent store / PostgreSQL for durability.
    """

    incident_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: IncidentStatus = IncidentStatus.DETECTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Phase outputs
    anomaly_event: AnomalyEvent | None = None
    diagnosis_result: DiagnosisResult | None = None
    action_results: list[ActionResult] = Field(default_factory=list)
    verification_result: VerificationResult | None = None
    audit_trail: list[dict[str, Any]] = Field(default_factory=list)

    # Resolution metadata
    resolved_at: datetime | None = None
    time_to_detect_seconds: float | None = None
    time_to_mitigate_seconds: float | None = None
    auto_resolved: bool = False
    false_positive: bool = False
    human_overrode: bool = False
    human_feedback: str | None = None
    regressed: bool | None = Field(
        default=None,
        description="True if the same alert re-fired after the fix, False if confirmed stable, None if unknown",
    )

    # Cost tracking
    total_llm_tokens_used: int = 0
    total_llm_cost_usd: float = 0.0

    # Sandboxed intelligence layer: memory linkage and the SRE-facing brief
    episode_id: str | None = None
    duplicate_of: str | None = None
    handling_branch: str | None = None  # critical_rca | auto_fix | recommend
    skills_used: list[str] = Field(default_factory=list)
    brief: dict[str, Any] | None = None


# ─── Normalized Telemetry Schema (Universal Observability API Contract) ─


class NormalizedTelemetryRecord(BaseModel):
    """
    Standard internal schema for telemetry ingested via Observability Tool APIs.

    Replaces raw log/Kafka stream ingestion. All external platforms (Datadog, Splunk,
    Prometheus, Simulator) are normalized to this schema by their respective adapters.
    """

    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    entity: str = Field(..., description="Service or infrastructure entity identifier (e.g. payment-service-prod)")
    environment: str = Field(default="production", description="Environment: production, staging, etc.")
    connector_source: TelemetryConnectorSource = Field(
        default=TelemetryConnectorSource.SIMULATOR,
        description="Observability tool API source (Datadog, Splunk, Prometheus, Simulator)",
    )
    signal_type: TelemetrySignalType = Field(
        default=TelemetrySignalType.METRIC,
        description="Type of telemetry signal: metric, log_summary, trace, synthetic_probe, business_signal",
    )
    metric_name: str = Field(..., description="Name of the SLI / metric (e.g. transaction_failure_rate, latency_p99)")
    value: float = Field(..., description="Observed numeric value")
    unit: str = Field(default="", description="Unit of measurement (ms, percent, req/sec, etc.)")
    baseline_mean: float | None = Field(
        default=None,
        description="Historical baseline mean calculated by observability platform",
    )
    deviation_sigma: float | None = Field(
        default=None,
        description="Standard deviation or anomaly score from baseline",
    )
    severity: Severity = Field(
        default=Severity.MEDIUM,
        description="Pre-assessed or derived severity level",
    )
    tags: dict[str, Any] = Field(
        default_factory=dict,
        description="Dimensions/tags (e.g. cluster, region, deployment_version, error_code)",
    )
    raw_query: str | None = Field(
        default=None,
        description="Optional API query string or PromQL/Splunk expression used to fetch this record",
    )
    raw_source: str | None = Field(
        default=None,
        description="Original source identifier if migrating from legacy telemetry",
    )
    raw_event_type: str | None = Field(
        default=None,
        description="Original event_type identifier if migrating from legacy telemetry",
    )
    ingested_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="When this system fetched the record; `timestamp` is when the source observed it",
    )
    is_simulated: bool = Field(
        default=False,
        description="True for synthetic data. Never treat as authoritative evidence for real incidents",
    )
    source_ref: str | None = Field(
        default=None,
        description="Link or identifier to the originating object (query URL, deployment URL, etc.)",
    )

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_telemetry(cls, data: Any) -> Any:  # noqa: ANN401 - Pydantic before validators accept arbitrary input.
        """Handle legacy constructor arguments for backwards-compatible migration."""
        if isinstance(data, dict):
            if "entity" not in data and "service_name" in data:
                data["entity"] = data.pop("service_name")
            if "connector_source" not in data and "source" in data:
                src = data.pop("source")
                data["raw_source"] = src
                try:
                    data["connector_source"] = TelemetryConnectorSource(src)
                except ValueError:
                    data["connector_source"] = TelemetryConnectorSource.CUSTOM
            if "signal_type" not in data and "event_type" in data:
                ev_type = data.pop("event_type")
                data["raw_event_type"] = ev_type
                try:
                    data["signal_type"] = TelemetrySignalType(ev_type)
                except ValueError:
                    data["signal_type"] = TelemetrySignalType.METRIC
            if "record_id" not in data and "event_id" in data:
                data["record_id"] = data.pop("event_id")
            if "payload" in data and ("metric_name" not in data or "value" not in data):
                payload = data.pop("payload")
                tags = data.setdefault("tags", {})
                tags.update(payload)
                if "metric_name" not in data:
                    numeric_keys = [k for k, v in payload.items() if isinstance(v, int | float)]
                    if numeric_keys:
                        data["metric_name"] = numeric_keys[0]
                        data["value"] = float(payload[numeric_keys[0]])
                    else:
                        data["metric_name"] = "observed_signal"
                        data["value"] = 1.0
        return data

    @model_validator(mode="after")
    def _derive_provenance(self) -> NormalizedTelemetryRecord:
        """Mark simulated data from the connector or legacy tags so consumers need not guess."""
        if not self.is_simulated:
            legacy = any(self.tags.get(k) for k in ("simulated", "is_simulated", "is_mock", "simulated_fallback"))
            if legacy or self.connector_source == TelemetryConnectorSource.SIMULATOR:
                self.is_simulated = True
        return self

    @property
    def freshness_seconds(self) -> float:
        """Age of the observation at ingestion time (never negative)."""
        return max(0.0, (self.ingested_at - self.timestamp).total_seconds())

    @property
    def event_id(self) -> str:
        return self.record_id

    @property
    def service_name(self) -> str:
        return self.entity

    @property
    def source(self) -> str:
        return self.raw_source or self.connector_source.value

    @property
    def event_type(self) -> str:
        return self.raw_event_type or self.signal_type.value

    @property
    def payload(self) -> dict[str, Any]:
        """Synthesized payload dictionary for consumers transitioning from raw event dictionaries."""
        base = {
            self.metric_name: self.value,
            "unit": self.unit,
            "baseline_mean": self.baseline_mean,
            "deviation_sigma": self.deviation_sigma,
        }
        base.update(self.tags)
        return base


# Aliases
TelemetryRecord = NormalizedTelemetryRecord
TelemetryEvent = NormalizedTelemetryRecord  # Backwards-compatibility alias during pipeline migration


# ─── Feedback Agent (Phase 7) ───────────────────────────────────


class SemanticReward(BaseModel):
    """
    Qualitative evaluation of an incident resolution by the Reward Agent.
    Acts as an 'LLM-as-a-Judge' signal for Reinforcement Learning.
    """

    logical_consistency: float = Field(
        ge=0.0, le=1.0, description="Did the RCA follow logically from the observed metrics?"
    )
    action_relevance: float = Field(
        ge=0.0, le=1.0, description="Was the action taken appropriate given the identified root cause?"
    )
    expert_accuracy: float = Field(ge=0.0, le=1.0, description="Were the specialized expert reports accurate and deep?")
    overall_quality_score: float = Field(
        ge=0.0, le=1.0, description="Final normalized quality score (0-1) across all dimensions"
    )
    justification: str = Field(..., description="Detailed explanation for the assigned qualitative scores")
