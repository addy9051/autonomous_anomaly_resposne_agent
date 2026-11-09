"""Data contracts for the RCA benchmark: scenarios (inputs and ground truth) and solver predictions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = 1

ChangeKind = Literal["deploy", "config", "resource_limit", "feature_flag", "infra"]
Category = Literal["network", "database", "application", "external", "infrastructure", "security", "unknown"]


class Alert(BaseModel):
    service: str
    anomaly_type: str
    severity: Literal["critical", "high", "medium", "low"] = "high"
    summary: str


class Change(BaseModel):
    """Something that changed in the environment before the alert."""

    change_id: str
    evidence_id: str
    kind: ChangeKind
    service: str
    summary: str
    offset_s: int = Field(description="Seconds relative to alert time; negative means before the alert")
    files: list[str] = Field(default_factory=list)


class Signal(BaseModel):
    """A telemetry observation or log-derived fact available to the solver."""

    evidence_id: str
    service: str
    metric: str
    value: float
    baseline: float | None = None
    offset_s: int = 0
    detail: str = ""


class GroundTruth(BaseModel):
    answerable: bool = Field(description="False when the available evidence cannot justify a conclusion")
    culprit_change_id: str | None = Field(description="None when no listed change caused the incident")
    category: Category
    fault_class: str
    rationale: str
    supporting_evidence_ids: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    schema_version: int = SCHEMA_VERSION
    scenario_id: str
    source: Literal["synthetic", "live"] = "synthetic"
    difficulty: Literal["easy", "medium", "hard"]
    alert: Alert
    signals: list[Signal] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    unavailable_sources: list[str] = Field(default_factory=list)
    ground_truth: GroundTruth

    @property
    def evidence_ids(self) -> set[str]:
        return {s.evidence_id for s in self.signals} | {c.evidence_id for c in self.changes}

    @model_validator(mode="after")
    def _check_consistency(self) -> Scenario:
        ids = [s.evidence_id for s in self.signals] + [c.evidence_id for c in self.changes]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.scenario_id}: duplicate evidence ids")
        change_ids = {c.change_id for c in self.changes}
        if len(change_ids) != len(self.changes):
            raise ValueError(f"{self.scenario_id}: duplicate change ids")
        truth = self.ground_truth
        if truth.culprit_change_id is not None and truth.culprit_change_id not in change_ids:
            raise ValueError(f"{self.scenario_id}: culprit {truth.culprit_change_id} is not a listed change")
        unknown = set(truth.supporting_evidence_ids) - self.evidence_ids
        if unknown:
            raise ValueError(f"{self.scenario_id}: supporting evidence ids not in scenario: {sorted(unknown)}")
        if not truth.answerable and truth.culprit_change_id is not None:
            raise ValueError(f"{self.scenario_id}: an unanswerable scenario cannot name a culprit")
        return self


class Suspect(BaseModel):
    change_id: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class Prediction(BaseModel):
    """What a solver returns for one scenario."""

    scenario_id: str
    suspects: list[Suspect] = Field(
        default_factory=list,
        description=(
            "Changes the solver implicates, ordered by score. Suspects with equal scores are tied and the tie "
            "counts against the solver. An empty list means no change is implicated."
        ),
    )
    category: Category = "unknown"
    abstained: bool = Field(default=False, description="True when the solver declines to conclude")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    cited_evidence_ids: list[str] = Field(default_factory=list)
    latency_s: float = 0.0
    error: str | None = None
    rationale: str = Field(default="", description="Short free-text conclusion, kept for auditing failures")
