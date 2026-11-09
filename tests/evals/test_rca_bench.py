"""RCA benchmark harness: determinism of the frozen suite, metric correctness and baseline discrimination."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from evals.rca_bench.agent_solver import (
    AgentSolver,
    _change_evidence,
    _signal_evidence,
    build_event,
    prediction_from_diagnosis,
)
from evals.rca_bench.generator import FAULT_CLASSES, generate_suite
from evals.rca_bench.metrics import (
    Pair,
    brier_score,
    compute_metrics,
    expected_calibration_error,
    is_correct,
    top_k_correct,
)
from evals.rca_bench.runner import (
    DEFAULT_SUITE,
    append_checkpoint,
    build_report,
    load_checkpoint,
    load_suite,
    run_solver,
    suite_sha256,
    summarize_reports,
)
from evals.rca_bench.schema import Prediction, Scenario, Suspect
from evals.rca_bench.solvers import (
    SOLVERS,
    AlertServiceRecentChangeSolver,
    AlwaysAbstainSolver,
    MostRecentChangeSolver,
    OracleSolver,
)
from shared.schemas import DiagnosisResult, EvidenceRecord, Hypothesis, RootCauseCategory

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="module")
def suite() -> list[Scenario]:
    return load_suite(DEFAULT_SUITE)


def test_frozen_suite_matches_generator(suite: list[Scenario]) -> None:
    regenerated = generate_suite(seed=0, per_class=5)
    assert [s.model_dump() for s in regenerated] == [s.model_dump() for s in suite]


def test_suite_covers_every_fault_class(suite: list[Scenario]) -> None:
    classes = {s.ground_truth.fault_class for s in suite}
    assert classes == {name for name, _, _ in FAULT_CLASSES}
    assert all(s.source == "synthetic" for s in suite)


def test_suite_includes_cases_without_a_change_culprit_and_unanswerable_cases(suite: list[Scenario]) -> None:
    assert any(s.ground_truth.answerable and s.ground_truth.culprit_change_id is None for s in suite)
    assert any(not s.ground_truth.answerable for s in suite)


def test_scenario_rejects_unlisted_culprit(suite: list[Scenario]) -> None:
    data = suite[0].model_dump()
    data["ground_truth"]["culprit_change_id"] = "chg-999"
    with pytest.raises(ValueError, match="not a listed change"):
        Scenario.model_validate(data)


def test_every_registered_solver_name_is_unique_and_callable() -> None:
    assert len(SOLVERS) == len(set(SOLVERS))
    assert all(callable(factory) for factory in SOLVERS.values())


async def test_oracle_reaches_ceiling_and_abstain_scores_zero_coverage(suite: list[Scenario]) -> None:
    _, oracle = await run_solver(OracleSolver(), suite)
    assert oracle.top1_change_accuracy == 1.0
    assert oracle.category_accuracy == 1.0
    assert oracle.abstention_recall == 1.0
    assert oracle.false_abstention_rate == 0.0
    assert oracle.citation_validity == 1.0

    _, abstain = await run_solver(AlwaysAbstainSolver(), suite)
    assert abstain.top1_change_accuracy == 0.0
    assert abstain.abstention_recall == 1.0
    assert abstain.coverage == 0.0


async def test_baselines_are_distinguishable_and_far_from_ceiling(suite: list[Scenario]) -> None:
    _, recent = await run_solver(MostRecentChangeSolver(), suite)
    _, same_service = await run_solver(AlertServiceRecentChangeSolver(), suite)
    assert recent.top1_change_accuracy is not None and same_service.top1_change_accuracy is not None
    assert recent.top1_change_accuracy < same_service.top1_change_accuracy < 0.8
    # Neither heuristic ever abstains, and both state fixed high confidence, so they are confidently wrong.
    assert recent.abstention_recall == 0.0
    assert recent.confident_error_rate is not None and recent.confident_error_rate > 0.3


async def test_decoy_classes_defeat_the_recency_heuristic(suite: list[Scenario]) -> None:
    _, recent = await run_solver(MostRecentChangeSolver(), suite)
    for name in ("bad_deploy_decoy_later", "same_service_later_noop", "dependency_outage", "traffic_surge"):
        assert recent.by_fault_class[name]["accuracy"] == 0.0, name


def _scenario_with_culprit(suite: list[Scenario]) -> Scenario:
    return next(s for s in suite if s.ground_truth.culprit_change_id)


def test_top_k_and_correctness_rules(suite: list[Scenario]) -> None:
    scenario = _scenario_with_culprit(suite)
    culprit = scenario.ground_truth.culprit_change_id
    assert culprit is not None
    other = next(c.change_id for c in scenario.changes if c.change_id != culprit)

    def pair(**kwargs: object) -> Pair:
        return Pair(scenario, Prediction(scenario_id=scenario.scenario_id, **kwargs))  # type: ignore[arg-type]

    second = pair(suspects=[Suspect(change_id=other), Suspect(change_id=culprit)], confidence=0.5)
    assert not top_k_correct(second, 1)
    assert top_k_correct(second, 3)
    assert not top_k_correct(pair(abstained=True), 3)
    assert not is_correct(pair(suspects=[Suspect(change_id=culprit)], error="boom"))


def test_no_culprit_scenario_requires_implicating_nothing(suite: list[Scenario]) -> None:
    scenario = next(s for s in suite if s.ground_truth.answerable and s.ground_truth.culprit_change_id is None)
    blame = Prediction(scenario_id=scenario.scenario_id, suspects=[Suspect(change_id=scenario.changes[0].change_id)])
    none_blamed = Prediction(scenario_id=scenario.scenario_id)
    assert not top_k_correct(Pair(scenario, blame), 3)
    assert top_k_correct(Pair(scenario, none_blamed), 1)


def test_unanswerable_scenario_is_correct_only_when_abstaining(suite: list[Scenario]) -> None:
    scenario = next(s for s in suite if not s.ground_truth.answerable)
    assert is_correct(Pair(scenario, Prediction(scenario_id=scenario.scenario_id, abstained=True)))
    assert not is_correct(Pair(scenario, Prediction(scenario_id=scenario.scenario_id, confidence=0.9)))


def test_calibration_metrics() -> None:
    perfect = [(1.0, True)] * 5 + [(0.0, False)] * 5
    assert expected_calibration_error(perfect) == 0.0
    assert brier_score(perfect) == 0.0
    always_sure_half_wrong = [(0.9, True), (0.9, False)] * 5
    assert expected_calibration_error(always_sure_half_wrong) == pytest.approx(0.4)
    assert expected_calibration_error([]) is None


def test_invalid_citations_lower_validity(suite: list[Scenario]) -> None:
    scenario = suite[0]
    real = next(iter(scenario.evidence_ids))
    pred = Prediction(scenario_id=scenario.scenario_id, cited_evidence_ids=[real, "ev-999"])
    assert compute_metrics([Pair(scenario, pred)]).citation_validity == 0.5


def _diagnosis(scenario: Scenario, text: str, category: RootCauseCategory, evidence_ref: str) -> DiagnosisResult:
    return DiagnosisResult(
        event_id="e1",
        root_cause=text,
        root_cause_category=category,
        hypotheses=[
            Hypothesis(
                hypothesis=text,
                supporting_evidence=[EvidenceRecord(source="custom", query_or_ref=evidence_ref)],
            )
        ],
        recommended_actions=[],
        confidence=0.6,
    )


def test_prediction_from_diagnosis_extracts_known_changes_only(suite: list[Scenario]) -> None:
    scenario = _scenario_with_culprit(suite)
    culprit = scenario.ground_truth.culprit_change_id
    evidence = scenario.signals[0].evidence_id
    diagnosis = _diagnosis(
        scenario, f"{culprit} caused it; chg-777 is invented", RootCauseCategory.APPLICATION, evidence
    )
    pred = prediction_from_diagnosis(diagnosis, scenario)
    assert [s.change_id for s in pred.suspects] == [culprit]
    assert pred.cited_evidence_ids == [evidence]
    assert pred.category == "application"
    assert not pred.abstained


def test_cited_change_evidence_implicates_the_change_even_when_text_paraphrases(suite: list[Scenario]) -> None:
    scenario = _scenario_with_culprit(suite)
    culprit = next(c for c in scenario.changes if c.change_id == scenario.ground_truth.culprit_change_id)
    diagnosis = _diagnosis(
        scenario, "A release regression in the service", RootCauseCategory.APPLICATION, culprit.evidence_id
    )
    pred = prediction_from_diagnosis(diagnosis, scenario)
    assert [s.change_id for s in pred.suspects] == [culprit.change_id]


class _CountingSolver:
    name = "counting"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def solve(self, scenario: Scenario) -> Prediction:
        self.calls.append(scenario.scenario_id)
        return Prediction(scenario_id=scenario.scenario_id, abstained=True)


class _HangingSolver:
    name = "hanging"

    async def solve(self, scenario: Scenario) -> Prediction:
        await asyncio.sleep(10)
        return Prediction(scenario_id=scenario.scenario_id)


async def test_checkpoint_resume_skips_finished_scenarios_and_retries_errors(
    suite: list[Scenario], tmp_path: Path
) -> None:
    sha = suite_sha256(DEFAULT_SUITE)
    checkpoint = tmp_path / "run.jsonl"
    append_checkpoint(checkpoint, sha, Prediction(scenario_id=suite[0].scenario_id, abstained=True))
    append_checkpoint(checkpoint, sha, Prediction(scenario_id=suite[1].scenario_id, error="boom"))
    append_checkpoint(checkpoint, "other-suite", Prediction(scenario_id=suite[2].scenario_id, abstained=True))

    completed = load_checkpoint(checkpoint, sha)
    assert set(completed) == {suite[0].scenario_id}

    solver = _CountingSolver()
    predictions, _ = await run_solver(solver, suite[:4], completed=completed)
    assert suite[0].scenario_id not in solver.calls
    assert solver.calls == [s.scenario_id for s in suite[1:4]]
    assert len(predictions) == 4


async def test_timeout_is_recorded_as_an_error_not_a_pass(suite: list[Scenario]) -> None:
    predictions, metrics = await run_solver(_HangingSolver(), suite[:1], timeout_s=0.05)
    assert predictions[0].error is not None
    assert "timeout" in predictions[0].error
    assert metrics.error_rate == 1.0
    assert metrics.top1_change_accuracy == 0.0


async def test_summarize_reports_shows_spread_and_misses(suite: list[Scenario], tmp_path: Path) -> None:
    paths = []
    for i, solver in enumerate([OracleSolver(), AlertServiceRecentChangeSolver()]):
        predictions, metrics = await run_solver(solver, suite)
        path = tmp_path / f"run{i}.json"
        report = build_report("mixed", suite, predictions, metrics, DEFAULT_SUITE)
        path.write_text(json.dumps(report), encoding="utf-8")
        paths.append(path)
    text = summarize_reports(paths)
    assert "runs: 2" in text
    assert "Mean (min-max)" in text
    assert "Top-1 change accuracy | 0.786 (0.571-1.000)" in text
    assert "Scenarios answered incorrectly" in text


def test_hedging_across_tied_suspects_does_not_earn_top1(suite: list[Scenario]) -> None:
    scenario = _scenario_with_culprit(suite)
    culprit = scenario.ground_truth.culprit_change_id
    assert culprit is not None
    everything = [Suspect(change_id=c.change_id, score=0.9) for c in scenario.changes]
    hedged = Pair(scenario, Prediction(scenario_id=scenario.scenario_id, suspects=everything, confidence=0.9))
    assert len(scenario.changes) > 1
    assert not top_k_correct(hedged, 1)
    assert top_k_correct(hedged, len(scenario.changes))

    ranked = [Suspect(change_id=culprit, score=0.9)] + [
        Suspect(change_id=c.change_id, score=0.5) for c in scenario.changes if c.change_id != culprit
    ]
    assert top_k_correct(Pair(scenario, Prediction(scenario_id=scenario.scenario_id, suspects=ranked)), 1)


def test_low_confidence_and_category_distribution_metrics(suite: list[Scenario]) -> None:
    answerable = next(s for s in suite if s.ground_truth.answerable)
    unanswerable = next(s for s in suite if not s.ground_truth.answerable)
    pairs = [
        Pair(answerable, Prediction(scenario_id=answerable.scenario_id, category="application", confidence=0.9)),
        Pair(unanswerable, Prediction(scenario_id=unanswerable.scenario_id, category="application", confidence=0.4)),
    ]
    metrics = compute_metrics(pairs)
    assert metrics.low_confidence_when_unanswerable == 1.0
    assert metrics.low_confidence_when_answerable == 0.0
    assert metrics.predicted_categories == {"application": 2}


def test_agent_mapping_groups_top_hypothesis_changes_as_tied(suite: list[Scenario]) -> None:
    scenario = next(s for s in suite if len(s.changes) >= 3 and s.ground_truth.culprit_change_id)
    first, second, third = (c for c in scenario.changes[:3])
    diagnosis = DiagnosisResult(
        event_id="e1",
        root_cause="A release regression",
        root_cause_category=RootCauseCategory.APPLICATION,
        hypotheses=[
            Hypothesis(
                hypothesis="top",
                supporting_evidence=[
                    EvidenceRecord(source="custom", query_or_ref=first.evidence_id),
                    EvidenceRecord(source="custom", query_or_ref=second.evidence_id),
                ],
            ),
            Hypothesis(
                hypothesis="alternative",
                supporting_evidence=[EvidenceRecord(source="custom", query_or_ref=third.evidence_id)],
            ),
        ],
        recommended_actions=[],
        confidence=0.8,
    )
    pred = prediction_from_diagnosis(diagnosis, scenario)
    scores = {s.change_id: s.score for s in pred.suspects}
    assert scores[first.change_id] == scores[second.change_id] > scores[third.change_id]
    assert pred.rationale == "A release regression"


class _SlowSolver:
    name = "slow"

    def __init__(self) -> None:
        self.in_flight = 0
        self.peak = 0

    async def solve(self, scenario: Scenario) -> Prediction:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        await asyncio.sleep(0.05)
        self.in_flight -= 1
        return Prediction(scenario_id=scenario.scenario_id, abstained=True)


async def test_concurrency_is_bounded_ordered_and_counts_progress(suite: list[Scenario]) -> None:
    solver = _SlowSolver()
    seen: list[int] = []
    predictions, _ = await run_solver(
        solver, suite[:8], concurrency=3, on_prediction=lambda done, total, s, p, secs: seen.append(done)
    )
    assert solver.peak == 3
    assert [p.scenario_id for p in predictions] == [s.scenario_id for s in suite[:8]]
    assert seen == list(range(1, 9))


async def test_timeout_counts_from_start_not_from_waiting_for_a_slot(suite: list[Scenario]) -> None:
    solver = _SlowSolver()
    predictions, _ = await run_solver(solver, suite[:4], concurrency=1, timeout_s=0.2)
    assert all(p.error is None for p in predictions)


class _FakeAgent:
    """Reads evidence through the same module functions the real graph uses, interleaving with other tasks."""

    def __init__(self) -> None:
        self.seen: list[list[tuple[str, str]]] = []

    async def diagnose(self, event: object) -> DiagnosisResult:
        from agents.diagnosis import evidence as evidence_module

        signals, _ = await evidence_module.collect_evidence(object(), ["x"])
        await asyncio.sleep(0.01)
        changes, _ = await evidence_module.collect_deploy_evidence()
        records = [*signals, *changes]
        self.seen.append(sorted((e["query_or_ref"], e["metric_name"]) for e in records))
        return DiagnosisResult(
            event_id="e1",
            root_cause="x",
            root_cause_category=RootCauseCategory.APPLICATION,
            recommended_actions=[],
            confidence=0.5,
        )


class _NeverReadsEvidenceAgent:
    async def diagnose(self, event: object) -> DiagnosisResult:
        return DiagnosisResult(
            event_id="e1",
            root_cause="x",
            root_cause_category=RootCauseCategory.APPLICATION,
            recommended_actions=[],
            confidence=0.5,
        )


async def test_concurrent_agent_solves_never_see_each_others_evidence(suite: list[Scenario]) -> None:
    from datetime import UTC, datetime

    scenarios = suite[:12]
    solver = AgentSolver()
    agent = _FakeAgent()
    solver._agent = agent
    predictions, _ = await run_solver(solver, scenarios, concurrency=6)
    assert all(p.error is None for p in predictions)

    now = datetime.now(UTC)
    expected = sorted(
        sorted((e["query_or_ref"], e["metric_name"]) for e in [*_signal_evidence(s, now), *_change_evidence(s, now)])
        for s in scenarios
    )
    assert sorted(agent.seen) == expected


async def test_run_where_evidence_was_never_requested_is_an_error_not_a_score(suite: list[Scenario]) -> None:
    solver = AgentSolver()
    solver._agent = _NeverReadsEvidenceAgent()
    predictions, metrics = await run_solver(solver, suite[:1])
    assert predictions[0].error is not None
    assert "never requested" in predictions[0].error
    assert metrics.error_rate == 1.0


async def test_skip_runbook_search_fails_fast_and_patches_are_removed_afterwards(suite: list[Scenario]) -> None:
    from knowledge_base.retrieval import search as search_module

    original = search_module.HybridSearchService
    solver = AgentSolver(skip_runbook_search=True)
    solver._agent = _FakeAgent()
    await solver.solve(suite[0])
    stub = search_module.HybridSearchService()
    assert (await stub.healthcheck())["status"] == "unavailable"
    with pytest.raises(RuntimeError, match="disabled"):
        await stub.search(query="x")
    solver.close()
    assert search_module.HybridSearchService is original


def test_unknown_category_counts_as_abstention(suite: list[Scenario]) -> None:
    scenario = suite[0]
    diagnosis = _diagnosis(scenario, "cannot tell", RootCauseCategory.UNKNOWN, "not-in-scenario")
    pred = prediction_from_diagnosis(diagnosis, scenario)
    assert pred.abstained
    assert pred.cited_evidence_ids == []


def test_replayed_evidence_exposes_change_details_and_ids(suite: list[Scenario]) -> None:
    from datetime import UTC, datetime

    scenario = _scenario_with_culprit(suite)
    now = datetime.now(UTC)
    changes = _change_evidence(scenario, now)
    signals = _signal_evidence(scenario, now)
    culprit = next(c for c in scenario.changes if c.change_id == scenario.ground_truth.culprit_change_id)

    culprit_record = next(e for e in changes if e["query_or_ref"] == culprit.evidence_id)
    assert culprit.change_id in culprit_record["metric_name"]
    assert culprit.summary in culprit_record["metric_name"]
    assert all(not e["simulated"] for e in [*changes, *signals])
    assert build_event(scenario).affected_services[0] == scenario.alert.service
