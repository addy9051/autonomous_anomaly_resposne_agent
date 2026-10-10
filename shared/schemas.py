"""
Pydantic v2 schemas — inter-agent API contracts.

Every message exchanged between agents is validated against these models.
If LLM output fails validation, the agent retries up to 3 times with a
"fix the JSON" prompt before escalating with raw output to human review.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
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


class TelemetryConnectorSource(StrEnum):
    SIMULATOR = "simulator"
    DATADOG = "datadog"
    SPLUNK = "splunk"
    PROMETHEUS = "prometheus"
    SNOWFLAKE = "snowflake"
    CUSTOM = "custom"


class ActionTier(int, Enum):
    """Tier 1 = autonomous, Tier 2 = needs SRE approval, Tier 3 = always human."""

    TIER_1_AUTO = 1
    TIER_2_APPROVE = 2
    TIER_3_HUMAN = 3


class IncidentStatus(StrEnum):
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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
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
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Full Incident Record ───────────────────────────────────────


class IncidentRecord(BaseModel):
    """
    Complete lifecycle record for an incident.
    Stored in PostgreSQL/Spanner for durability.
    """

    incident_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: IncidentStatus = IncidentStatus.DETECTED
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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


# ─── Normalized Telemetry Schema (Universal Observability API Contract) ─


class NormalizedTelemetryRecord(BaseModel):
    """
    Standard internal schema for telemetry ingested via Observability Tool APIs.

    Replaces raw log/Kafka stream ingestion. All external platforms (Datadog, Splunk,
    Prometheus, Simulator) are normalized to this schema by their respective adapters.
    """

    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
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

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_telemetry(cls, data: Any) -> Any:
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
                    numeric_keys = [k for k, v in payload.items() if isinstance(v, (int, float))]
                    if numeric_keys:
                        data["metric_name"] = numeric_keys[0]
                        data["value"] = float(payload[numeric_keys[0]])
                    else:
                        data["metric_name"] = "observed_signal"
                        data["value"] = 1.0
        return data

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
