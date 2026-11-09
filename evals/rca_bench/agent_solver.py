"""Runs the real DiagnosisAgent on a scenario by replaying the scenario's evidence into it.

Experimental: the conversion logic and concurrency isolation are unit tested; the full run needs a reachable
LLM endpoint, so it is run by hand rather than in CI.

Concurrency: scenarios may be solved in parallel. Module patches are installed once and each scenario's
evidence travels in a context variable, so concurrent tasks cannot see each other's data. A run whose
evidence was never requested is reported as an error instead of being scored.

How a scenario is presented to the agent:
- Signals and changes replace the telemetry adapter and deployment lookups, in the shape the production
  evidence collector produces, so grounding, citation checks and confidence caps still run unchanged.
- The agent's evidence index shows only ``metric_name`` and ``value`` per record, not descriptions or tags.
  Until that rendering carries change details, the harness encodes the change id, summary and files, and any
  signal detail, into ``metric_name`` so the model can actually read them.
- Replayed records are marked authoritative (not simulated). Simulated records would force the agent's
  confidence caps and hide what it can do. The suite itself is labelled synthetic.
- Services named by signals are added to the alert's affected services (up to the collector's limit).

Mapping back to a Prediction (scoring version 2):
- A suspect is a listed change the agent implicates by id in its root cause or hypotheses, or whose evidence
  record a hypothesis cites as support. Changes in the root cause and the top hypothesis share one score, so
  blaming several changes counts as hedging (ties count against the agent). Later hypotheses add lower groups.
  Text that rules a change out by id would still implicate it.
- The agent abstains only by reporting root_cause_category "unknown". Low confidence is reported separately.
"""

from __future__ import annotations

import re
import time
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from agents.diagnosis.evidence import record_to_evidence
from evals.rca_bench.prompt_variants import synthesis_prompt_for
from evals.rca_bench.schema import Prediction, Scenario, Suspect
from shared.schemas import (
    AnomalyEvent,
    AnomalyType,
    MetricsSnapshot,
    NormalizedTelemetryRecord,
    Severity,
    TelemetryConnectorSource,
    TelemetrySignalType,
)

if TYPE_CHECKING:
    from shared.schemas import DiagnosisResult, Hypothesis

CHANGE_ID_PATTERN = re.compile(r"chg-\d{3}")
MAX_SERVICES = 3


def _services(scenario: Scenario) -> list[str]:
    services = [scenario.alert.service]
    for signal in scenario.signals:
        if signal.service not in services:
            services.append(signal.service)
    return services[:MAX_SERVICES]


def build_event(scenario: Scenario) -> AnomalyEvent:
    return AnomalyEvent(
        severity=Severity(scenario.alert.severity),
        affected_services=_services(scenario),
        anomaly_type=AnomalyType(scenario.alert.anomaly_type),
        metrics_snapshot=MetricsSnapshot(),
        reasoning=scenario.alert.summary,
        confidence=0.9,
    )


def _signal_evidence(scenario: Scenario, base_time: datetime) -> list[dict[str, Any]]:
    evidence = []
    for s in scenario.signals:
        name = f"{s.metric} [{s.detail}]" if s.detail else s.metric
        rec = NormalizedTelemetryRecord(
            timestamp=base_time + timedelta(seconds=s.offset_s),
            entity=s.service,
            connector_source=TelemetryConnectorSource.CUSTOM,
            signal_type=TelemetrySignalType.METRIC,
            metric_name=name,
            value=s.value,
            baseline_mean=s.baseline,
            source_ref=s.evidence_id,
        )
        evidence.append(record_to_evidence(rec))
    return evidence


def _change_evidence(scenario: Scenario, base_time: datetime) -> list[dict[str, Any]]:
    evidence = []
    for c in scenario.changes:
        files = f" files: {', '.join(c.files)}" if c.files else ""
        rec = NormalizedTelemetryRecord(
            timestamp=base_time + timedelta(seconds=c.offset_s),
            entity=c.service,
            connector_source=TelemetryConnectorSource.CUSTOM,
            signal_type=TelemetrySignalType.DEPLOY_EVENT,
            metric_name=f"{c.change_id} {c.kind} on {c.service}: {c.summary}.{files}",
            value=1.0,
            source_ref=c.evidence_id,
        )
        evidence.append(record_to_evidence(rec))
    return evidence


def prediction_from_diagnosis(diagnosis: DiagnosisResult, scenario: Scenario) -> Prediction:
    """Map the agent's output onto the benchmark's prediction contract.

    Changes implicated by the root cause or the top hypothesis form the primary group and share one score, so
    a diagnosis that blames several changes is hedging and scores as tied. Each later hypothesis adds a lower
    group. Hypotheses arrive best-supported first.
    """
    known = {c.change_id for c in scenario.changes}
    change_by_evidence = {c.evidence_id: c.change_id for c in scenario.changes}

    def mentioned(text: str | None) -> list[str]:
        return [c for c in CHANGE_ID_PATTERN.findall(text or "") if c in known]

    def cited_changes(hypothesis: Hypothesis) -> list[str]:
        return [
            change_by_evidence[r.query_or_ref]
            for r in hypothesis.supporting_evidence
            if r.query_or_ref in change_by_evidence
        ]

    groups: list[list[str]] = []
    seen: set[str] = set()

    def add_group(ids: list[str]) -> None:
        fresh = [i for i in dict.fromkeys(ids) if i not in seen]
        if fresh:
            groups.append(fresh)
            seen.update(fresh)

    primary = mentioned(diagnosis.root_cause)
    if diagnosis.hypotheses:
        primary += mentioned(diagnosis.hypotheses[0].hypothesis) + cited_changes(diagnosis.hypotheses[0])
    add_group(primary)
    for hypothesis in diagnosis.hypotheses[1:]:
        add_group(mentioned(hypothesis.hypothesis) + cited_changes(hypothesis))
    add_group(mentioned(diagnosis.decision_summary))

    confidence = diagnosis.confidence
    suspects = [
        Suspect(change_id=change_id, score=round(confidence * 0.8**rank, 4))
        for rank, group in enumerate(groups)
        for change_id in group
    ]

    cited: list[str] = []
    for hypothesis in diagnosis.hypotheses:
        for record in hypothesis.supporting_evidence:
            if record.query_or_ref in scenario.evidence_ids and record.query_or_ref not in cited:
                cited.append(record.query_or_ref)

    return Prediction(
        scenario_id=scenario.scenario_id,
        suspects=suspects,
        category=diagnosis.root_cause_category.value,
        abstained=diagnosis.root_cause_category.value == "unknown",
        confidence=confidence,
        cited_evidence_ids=cited,
        rationale=diagnosis.root_cause[:600],
    )


class _NoRunbookSearch:
    """Stands in for HybridSearchService when no knowledge base is available.

    The production path already treats an unavailable knowledge base as "no runbooks" after a slow failed
    connection attempt; this fails immediately with the same outcome.
    """

    async def healthcheck(self) -> dict[str, Any]:
        return {"status": "unavailable", "document_count": 0}

    async def search(self, *_args: object, **_kwargs: object) -> list[Any]:
        raise RuntimeError("runbook search disabled for this benchmark run")


@dataclass
class _Replay:
    """One scenario's evidence, visible only to the task that is diagnosing it."""

    signals: list[dict[str, Any]]
    changes: list[dict[str, Any]]
    telemetry_down: bool
    deploys_down: bool
    collect_calls: int = field(default=0)


_REPLAY: ContextVar[_Replay | None] = ContextVar("rca_bench_replay", default=None)


def _current_replay() -> _Replay:
    replay = _REPLAY.get()
    if replay is None:
        raise RuntimeError("benchmark replay context is not visible to the diagnosis graph")
    return replay


async def _replay_collect(
    _adapter: object, services: list[str], *_args: object, **_kwargs: object
) -> tuple[list[dict[str, Any]], list[str]]:
    replay = _current_replay()
    replay.collect_calls += 1
    if replay.telemetry_down:
        return [], [f"custom:{svc}:*" for svc in services]
    return sorted(replay.signals, key=lambda e: e["observed_at"]), []


async def _replay_deploys(*_args: object, **_kwargs: object) -> tuple[list[dict[str, Any]], list[str]]:
    replay = _current_replay()
    return ([], ["github:deployments"]) if replay.deploys_down else (replay.changes, [])


class AgentSolver:
    """Safe to run concurrently: the module patches are installed once and read per-task context variables."""

    name = "agent"

    def __init__(self, skip_runbook_search: bool = False, prompt_variant: str = "baseline") -> None:
        # Validate now so a typo fails before any LLM call is made.
        synthesis_prompt_for(prompt_variant, "")
        self._agent: Any = None
        self._skip_runbook_search = skip_runbook_search
        self._prompt_variant = prompt_variant
        self._patches: ExitStack | None = None

    def _install_patches(self) -> None:
        if self._patches is not None:
            return
        stack = ExitStack()
        stack.enter_context(patch("agents.diagnosis.evidence.collect_evidence", new=_replay_collect))
        stack.enter_context(patch("agents.diagnosis.evidence.collect_deploy_evidence", new=_replay_deploys))
        stack.enter_context(patch("data_pipeline.connectors.adapters.get_telemetry_adapter", new=lambda: object()))
        if self._skip_runbook_search:
            stack.enter_context(patch("knowledge_base.retrieval.search.HybridSearchService", new=_NoRunbookSearch))
        if self._prompt_variant != "baseline":
            from agents.diagnosis.prompts import SYNTHESIS_PROMPT

            variant_prompt = synthesis_prompt_for(self._prompt_variant, SYNTHESIS_PROMPT)
            stack.enter_context(patch("agents.diagnosis.graph.SYNTHESIS_PROMPT", new=variant_prompt))
        self._patches = stack

    def close(self) -> None:
        if self._patches is not None:
            self._patches.close()
            self._patches = None

    async def solve(self, scenario: Scenario) -> Prediction:
        if self._agent is None:
            from agents.diagnosis.graph import DiagnosisAgent

            self._agent = DiagnosisAgent()
        self._install_patches()

        base_time = datetime.now(UTC)
        replay = _Replay(
            signals=_signal_evidence(scenario, base_time),
            changes=_change_evidence(scenario, base_time),
            telemetry_down="telemetry" in scenario.unavailable_sources,
            deploys_down="deploys" in scenario.unavailable_sources,
        )
        token = _REPLAY.set(replay)
        started = time.perf_counter()
        try:
            diagnosis = await self._agent.diagnose(build_event(scenario))
        except Exception as exc:  # noqa: BLE001 - a failed run is recorded as an error, never a silent pass
            return Prediction(scenario_id=scenario.scenario_id, error=f"{type(exc).__name__}: {exc}")
        finally:
            _REPLAY.reset(token)

        # The graph swallows evidence-collection failures. If the replay was never read, the agent ran on no
        # evidence at all, and scoring that run would be meaningless.
        if replay.collect_calls == 0:
            return Prediction(
                scenario_id=scenario.scenario_id,
                error="replay evidence was never requested; scenario context did not reach the diagnosis graph",
            )

        prediction = prediction_from_diagnosis(diagnosis, scenario)
        prediction.latency_s = round(time.perf_counter() - started, 3)
        return prediction
