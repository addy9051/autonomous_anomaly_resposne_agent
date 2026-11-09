"""RCA benchmark harness: determinism of the frozen suite, metric correctness and baseline discrimination."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

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
    from collections.abc import Callable
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


def test_holdout_suite_is_frozen_and_distinct_from_the_dev_suite(suite: list[Scenario]) -> None:
    holdout_path = DEFAULT_SUITE.with_name("synthetic_v0_seed1.jsonl")
    holdout = load_suite(holdout_path)
    regenerated = generate_suite(seed=1, per_class=5)
    assert [s.model_dump() for s in regenerated] == [s.model_dump() for s in holdout]
    assert [s.model_dump() for s in holdout] != [s.model_dump() for s in suite]
    assert suite_sha256(holdout_path) != suite_sha256(DEFAULT_SUITE)


def test_prompt_variants_format_cleanly_and_reject_unknown_names() -> None:
    from agents.diagnosis.graph import build_synthesis_prompt
    from agents.diagnosis.prompts import SYNTHESIS_PROMPT
    from evals.rca_bench.prompt_variants import VARIANTS, synthesis_prompt_for

    assert synthesis_prompt_for("baseline", SYNTHESIS_PROMPT) == SYNTHESIS_PROMPT
    assert synthesis_prompt_for("evidence_discipline", SYNTHESIS_PROMPT).startswith(SYNTHESIS_PROMPT.rstrip())
    with pytest.raises(ValueError, match="unknown prompt variant"):
        synthesis_prompt_for("evidence_disciplin", SYNTHESIS_PROMPT)

    state = {"anomaly_event": {"event_id": "e1"}, "context": "c", "evidence_timeline": [], "missing_sources": []}
    for name in VARIANTS:
        with patch("agents.diagnosis.graph.SYNTHESIS_PROMPT", new=synthesis_prompt_for(name, SYNTHESIS_PROMPT)):
            assert "Citable Evidence" in build_synthesis_prompt(state)  # type: ignore[arg-type]


async def test_prompt_variant_is_applied_during_a_run_and_restored_afterwards(suite: list[Scenario]) -> None:
    from agents.diagnosis import graph

    original = graph.SYNTHESIS_PROMPT
    solver = AgentSolver(prompt_variant="evidence_discipline")
    solver._agent = _FakeAgent()
    await solver.solve(suite[0])
    assert "Rules for choosing the cause and the category" in graph.SYNTHESIS_PROMPT
    solver.close()
    assert original == graph.SYNTHESIS_PROMPT
    with pytest.raises(ValueError, match="unknown prompt variant"):
        AgentSolver(prompt_variant="nope")


async def test_warm_up_refuses_a_non_allowlisted_endpoint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The warm-up request must go through the egress guard like every other HTTP client."""
    from evals.rca_bench.__main__ import _warm_up
    from shared.config import get_settings

    monkeypatch.setattr(get_settings().app, "egress_enforce", True)
    monkeypatch.setattr(get_settings().llm, "base_url", "http://93.184.216.34/v1")  # public IP literal, no DNS
    assert await _warm_up(5.0) is False
    assert "EgressViolationError" in capsys.readouterr().err


class _ScriptedAgent:
    """Reads evidence like the real graph, then returns a diagnosis built from the scenario."""

    def __init__(self, build: Callable[[Scenario], DiagnosisResult], scenario: Scenario) -> None:
        self._build = build
        self._scenario = scenario

    async def diagnose(self, event: object) -> DiagnosisResult:
        from agents.diagnosis import evidence as evidence_module

        await evidence_module.collect_evidence(object(), ["x"])
        return self._build(self._scenario)


def _blaming(change_ids: list[str], confidence: float = 0.9) -> Callable[[Scenario], DiagnosisResult]:
    def build(scenario: Scenario) -> DiagnosisResult:
        by_id = {c.change_id: c for c in scenario.changes}
        return DiagnosisResult(
            event_id="e1",
            root_cause=" and ".join(f"{c} caused it" for c in change_ids),
            root_cause_category=RootCauseCategory.APPLICATION,
            hypotheses=[
                Hypothesis(
                    hypothesis="top",
                    supporting_evidence=[
                        EvidenceRecord(source="custom", query_or_ref=by_id[c].evidence_id) for c in change_ids
                    ],
                )
            ],
            recommended_actions=[],
            confidence=confidence,
        )

    return build


def _first(suite: list[Scenario], fault_class: str) -> Scenario:
    return next(s for s in suite if s.ground_truth.fault_class == fault_class)


async def test_temporal_mode_rules_out_a_change_that_landed_after_the_symptoms(suite: list[Scenario]) -> None:
    scenario = _first(suite, "dependency_outage")
    latest = max(scenario.changes, key=lambda c: c.offset_s)
    solver = AgentSolver(attribution="temporal")
    solver._agent = _ScriptedAgent(_blaming([latest.change_id]), scenario)
    prediction = await solver.solve(scenario)
    assert prediction.suspects == []
    assert latest.evidence_id not in prediction.cited_evidence_ids
    assert prediction.confidence <= 0.4
    assert top_k_correct(Pair(scenario, prediction), 1)  # no listed change is responsible here


async def test_temporal_mode_keeps_the_older_culprit_and_drops_later_decoys(suite: list[Scenario]) -> None:
    scenario = _first(suite, "bad_deploy_decoy_later")
    culprit = scenario.ground_truth.culprit_change_id
    assert culprit is not None
    everything = [c.change_id for c in scenario.changes]
    solver = AgentSolver(attribution="temporal")
    solver._agent = _ScriptedAgent(_blaming(everything), scenario)
    prediction = await solver.solve(scenario)
    assert [s.change_id for s in prediction.suspects] == [culprit]
    assert top_k_correct(Pair(scenario, prediction), 1)

    hedging = AgentSolver(attribution="baseline")
    hedging._agent = _ScriptedAgent(_blaming(everything), scenario)
    assert not top_k_correct(Pair(scenario, await hedging.solve(scenario)), 1)


def _decision(scenario: Scenario, kind: str, change_id: str | None = None) -> Any:  # noqa: ANN401
    from agents.diagnosis.attribution import Attribution

    async def decide(_summary: str, evidence: list[dict[str, Any]]) -> Attribution:
        if kind != "change":
            return Attribution(kind, category="external" if kind == "none" else None)
        target = next(c for c in scenario.changes if c.change_id == change_id)
        position = next(i for i, e in enumerate(evidence) if e["query_or_ref"] == target.evidence_id)
        return Attribution("change", f"E{position + 1}", "application")

    return decide


async def test_decompose_mode_uses_the_narrow_decision_for_attribution(suite: list[Scenario]) -> None:
    scenario = _first(suite, "bad_deploy_decoy_later")
    culprit = scenario.ground_truth.culprit_change_id
    assert culprit is not None
    wrong = next(c.change_id for c in scenario.changes if c.change_id != culprit)

    solver = AgentSolver(attribution="decompose")
    solver._agent = _ScriptedAgent(_blaming([culprit]), scenario)
    with patch("evals.rca_bench.agent_solver.decide_attribution", _decision(scenario, "change", culprit)):
        agree = await solver.solve(scenario)
    assert [s.change_id for s in agree.suspects] == [culprit]
    assert agree.confidence == 0.9

    solver._agent = _ScriptedAgent(_blaming([wrong]), scenario)
    with patch("evals.rca_bench.agent_solver.decide_attribution", _decision(scenario, "change", culprit)):
        disagree = await solver.solve(scenario)
    assert [s.change_id for s in disagree.suspects] == [culprit]
    assert disagree.confidence <= 0.4  # the two answers conflict, so confidence is capped


async def test_decompose_mode_maps_cannot_tell_to_abstention_and_none_to_no_suspects(suite: list[Scenario]) -> None:
    scenario = _first(suite, "insufficient_evidence")
    solver = AgentSolver(attribution="decompose")
    solver._agent = _ScriptedAgent(_blaming([scenario.changes[0].change_id]), scenario)
    with patch("evals.rca_bench.agent_solver.decide_attribution", _decision(scenario, "cannot_tell")):
        unsure = await solver.solve(scenario)
    assert (unsure.abstained, unsure.category, unsure.confidence, unsure.suspects) == (True, "unknown", 0.0, [])
    assert is_correct(Pair(scenario, unsure))

    with patch("evals.rca_bench.agent_solver.decide_attribution", _decision(scenario, "none")):
        none = await solver.solve(scenario)
    assert (none.abstained, none.category, none.suspects) == (False, "external", [])


async def test_decompose_temporal_turns_a_late_chosen_change_into_none(suite: list[Scenario]) -> None:
    scenario = _first(suite, "dependency_outage")
    latest = max(scenario.changes, key=lambda c: c.offset_s)
    solver = AgentSolver(attribution="decompose_temporal")
    solver._agent = _ScriptedAgent(_blaming([latest.change_id]), scenario)
    with patch("evals.rca_bench.agent_solver.decide_attribution", _decision(scenario, "change", latest.change_id)):
        prediction = await solver.solve(scenario)
    assert prediction.suspects == []
    assert "ruled out" in prediction.rationale


def test_attribution_modes_are_validated() -> None:
    from evals.rca_bench.prompt_variants import ATTRIBUTION_MODES

    assert set(ATTRIBUTION_MODES) == {
        "baseline",
        "temporal",
        "decompose",
        "decompose_temporal",
        "temporal_cap",
        "judge",
        "judge_temporal",
    }
    with pytest.raises(ValueError, match="unknown attribution mode"):
        AgentSolver(attribution="decompos")


def _verdicts(scenario: Scenario, evidence_for: dict[str, tuple[float, float, float]]) -> Any:  # noqa: ANN401
    """A stand-in for judge_candidates: change id -> (p_yes, p_no, p_unclear), keyed to the visible evidence ids."""
    from agents.diagnosis.candidate_judge import Verdict

    calls: list[frozenset[str]] = []

    async def judge(_summary: str, evidence: list[dict[str, Any]], exclude: frozenset[str] = frozenset()) -> list[Any]:
        calls.append(exclude)
        change_by_ref = {c.evidence_id: c.change_id for c in scenario.changes}
        out = []
        for position, record in enumerate(evidence):
            change_id = change_by_ref.get(record["query_or_ref"])
            eid = f"E{position + 1}"
            if change_id in evidence_for and eid not in exclude:
                yes, no, unclear = evidence_for[change_id]
                out.append(Verdict(eid, yes, no, unclear))
        return out

    judge.calls = calls  # type: ignore[attr-defined]
    return judge


async def test_judge_mode_names_the_supported_change_with_logprob_confidence(suite: list[Scenario]) -> None:
    scenario = _first(suite, "bad_deploy")
    culprit = scenario.ground_truth.culprit_change_id
    assert culprit is not None
    verdicts = {c.change_id: (0.1, 0.9, 0.0) for c in scenario.changes}
    verdicts[culprit] = (0.85, 0.15, 0.0)
    solver = AgentSolver(attribution="judge")
    solver._agent = _ScriptedAgent(_blaming([scenario.changes[0].change_id], confidence=0.99), scenario)
    with patch("evals.rca_bench.agent_solver.judge_candidates", _verdicts(scenario, verdicts)):
        prediction = await solver.solve(scenario)
    assert [s.change_id for s in prediction.suspects] == [culprit]
    assert prediction.confidence == 0.85  # from the probabilities, not the diagnosis's stated 0.99
    assert top_k_correct(Pair(scenario, prediction), 1)
    assert "[judge:change]" in prediction.rationale


async def test_judge_mode_abstains_when_there_are_no_symptoms_to_attribute(suite: list[Scenario]) -> None:
    scenario = next(s for s in suite if "telemetry" in s.unavailable_sources)
    verdicts = {c.change_id: (0.95, 0.05, 0.0) for c in scenario.changes}  # the model would happily blame a change
    solver = AgentSolver(attribution="judge")
    solver._agent = _ScriptedAgent(_blaming([scenario.changes[0].change_id]), scenario)
    with patch("evals.rca_bench.agent_solver.judge_candidates", _verdicts(scenario, verdicts)):
        prediction = await solver.solve(scenario)
    assert (prediction.abstained, prediction.category, prediction.suspects, prediction.confidence) == (
        True,
        "unknown",
        [],
        0.0,
    )
    assert is_correct(Pair(scenario, prediction))


async def test_judge_mode_abstains_when_two_changes_are_equally_plausible(suite: list[Scenario]) -> None:
    scenario = next(
        s
        for s in suite
        if s.ground_truth.fault_class == "insufficient_evidence" and "telemetry" not in s.unavailable_sources
    )
    verdicts = {c.change_id: (0.9, 0.1, 0.0) for c in scenario.changes}
    solver = AgentSolver(attribution="judge")
    solver._agent = _ScriptedAgent(_blaming([scenario.changes[0].change_id]), scenario)
    with patch("evals.rca_bench.agent_solver.judge_candidates", _verdicts(scenario, verdicts)):
        prediction = await solver.solve(scenario)
    assert prediction.abstained
    assert is_correct(Pair(scenario, prediction))


async def test_judge_temporal_does_not_ask_about_changes_the_rule_ruled_out(suite: list[Scenario]) -> None:
    scenario = _first(suite, "dependency_outage")
    latest = max(scenario.changes, key=lambda c: c.offset_s)
    verdicts = {c.change_id: (0.05, 0.95, 0.0) for c in scenario.changes}
    judge = _verdicts(scenario, verdicts)
    solver = AgentSolver(attribution="judge_temporal")
    solver._agent = _ScriptedAgent(_blaming([latest.change_id]), scenario)
    with patch("evals.rca_bench.agent_solver.judge_candidates", judge):
        prediction = await solver.solve(scenario)
    (excluded,) = judge.calls
    assert excluded  # the late change was excluded from judging
    assert (prediction.abstained, prediction.suspects) == (False, [])
    assert top_k_correct(Pair(scenario, prediction), 1)

    plain = AgentSolver(attribution="judge")
    plain._agent = _ScriptedAgent(_blaming([latest.change_id]), scenario)
    judge_all = _verdicts(scenario, verdicts)
    with patch("evals.rca_bench.agent_solver.judge_candidates", judge_all):
        await plain.solve(scenario)
    assert judge_all.calls == [frozenset()]  # plain "judge" excludes nothing


async def test_temporal_cap_keeps_the_answer_and_caps_confidence_only_on_disagreement(suite: list[Scenario]) -> None:
    scenario = _first(suite, "bad_deploy_decoy_later")
    culprit = scenario.ground_truth.culprit_change_id
    assert culprit is not None
    wrong = next(c.change_id for c in scenario.changes if c.change_id != culprit)

    solver = AgentSolver(attribution="temporal_cap")
    solver._agent = _ScriptedAgent(_blaming([culprit]), scenario)
    with patch("evals.rca_bench.agent_solver.decide_attribution", _decision(scenario, "change", culprit)):
        agree = await solver.solve(scenario)
    assert [s.change_id for s in agree.suspects] == [culprit]
    assert agree.confidence == 0.9

    # The narrow question names a different change: the answer stays, the confidence drops.
    with patch("evals.rca_bench.agent_solver.decide_attribution", _decision(scenario, "change", wrong)):
        disagree = await solver.solve(scenario)
    assert [s.change_id for s in disagree.suspects] == [culprit]
    assert disagree.confidence <= 0.4
    assert top_k_correct(Pair(scenario, disagree), 1)

    # "cannot tell" does not abstain in this mode; it only lowers confidence.
    with patch("evals.rca_bench.agent_solver.decide_attribution", _decision(scenario, "cannot_tell")):
        unsure = await solver.solve(scenario)
    assert (unsure.abstained, [s.change_id for s in unsure.suspects]) == (False, [culprit])
    assert unsure.confidence <= 0.4


async def test_temporal_cap_applies_the_temporal_rule_to_both_answers(suite: list[Scenario]) -> None:
    scenario = _first(suite, "dependency_outage")
    latest = max(scenario.changes, key=lambda c: c.offset_s)
    solver = AgentSolver(attribution="temporal_cap")
    solver._agent = _ScriptedAgent(_blaming([latest.change_id]), scenario)
    # Both the diagnosis and the narrow question pick the late change; the rule turns both into "no change", so
    # they agree and the confidence is only the temporal rule's own cap, not a second penalty.
    with patch("evals.rca_bench.agent_solver.decide_attribution", _decision(scenario, "change", latest.change_id)):
        prediction = await solver.solve(scenario)
    assert prediction.suspects == []
    assert top_k_correct(Pair(scenario, prediction), 1)
    assert "capped" not in prediction.rationale


def test_the_fresh_seed_suites_are_frozen_and_distinct_from_the_others(suite: list[Scenario]) -> None:
    names = ("synthetic_v0.jsonl", "synthetic_v0_seed1.jsonl", "synthetic_v0_seed2.jsonl", "synthetic_v0_seed3.jsonl")
    for seed, name in ((2, "synthetic_v0_seed2.jsonl"), (3, "synthetic_v0_seed3.jsonl")):
        path = DEFAULT_SUITE.with_name(name)
        fresh = load_suite(path)
        assert [s.model_dump() for s in generate_suite(seed=seed, per_class=5)] == [s.model_dump() for s in fresh]
        for other in names:
            if other != name:
                assert [s.model_dump() for s in load_suite(DEFAULT_SUITE.with_name(other))] != [
                    s.model_dump() for s in fresh
                ]
        assert suite_sha256(path) not in {suite_sha256(DEFAULT_SUITE.with_name(o)) for o in names if o != name}


def test_the_temporal_rule_never_rules_out_a_true_culprit_in_any_suite(suite: list[Scenario]) -> None:
    from evals.rca_bench.agent_solver import ruled_out_changes

    others = [
        s
        for name in ("synthetic_v0_seed1.jsonl", "synthetic_v0_seed2.jsonl", "synthetic_v0_seed3.jsonl")
        for s in load_suite(DEFAULT_SUITE.with_name(name))
    ]
    for scenario in [*suite, *others]:
        culprit = scenario.ground_truth.culprit_change_id
        assert culprit is None or culprit not in ruled_out_changes(scenario), scenario.scenario_id
    # and it does remove something, so the test above is not vacuous
    assert any(ruled_out_changes(s) for s in suite)


def test_rescoring_removes_ruled_out_changes_and_caps_confidence_only_when_none_remain(suite: list[Scenario]) -> None:
    from evals.rca_bench.agent_solver import ruled_out_changes
    from evals.rca_bench.rescore import apply_temporal_rule_to_prediction

    scenario = _first(suite, "dependency_outage")
    late = sorted(ruled_out_changes(scenario))[0]
    only_late = Prediction(
        scenario_id=scenario.scenario_id,
        suspects=[Suspect(change_id=late, score=0.9)],
        confidence=0.9,
        cited_evidence_ids=[next(c.evidence_id for c in scenario.changes if c.change_id == late)],
    )
    rescored = apply_temporal_rule_to_prediction(only_late, scenario)
    assert rescored.suspects == []
    assert rescored.cited_evidence_ids == []
    assert rescored.confidence == 0.4
    assert top_k_correct(Pair(scenario, rescored), 1)

    culprit_scenario = _first(suite, "bad_deploy_decoy_later")
    culprit = culprit_scenario.ground_truth.culprit_change_id
    assert culprit is not None
    decoys = sorted(ruled_out_changes(culprit_scenario))
    hedged = Prediction(
        scenario_id=culprit_scenario.scenario_id,
        suspects=[Suspect(change_id=c, score=0.9) for c in [culprit, *decoys]],
        confidence=0.9,
    )
    fixed = apply_temporal_rule_to_prediction(hedged, culprit_scenario)
    assert [s.change_id for s in fixed.suspects] == [culprit]
    assert fixed.confidence == 0.9  # a suspect remains, so confidence is untouched

    failed = Prediction(scenario_id=scenario.scenario_id, error="boom")
    assert apply_temporal_rule_to_prediction(failed, scenario) is failed


def test_rescore_command_writes_reports_that_summarize_can_read(suite: list[Scenario], tmp_path: Path) -> None:
    from evals.rca_bench.__main__ import main
    from evals.rca_bench.agent_solver import ruled_out_changes

    scenario = _first(suite, "dependency_outage")
    late = sorted(ruled_out_changes(scenario))[0]
    predictions = [
        Prediction(scenario_id=s.scenario_id, suspects=[Suspect(change_id=late, score=0.9)] if s is scenario else [])
        for s in suite
    ]
    metrics = compute_metrics([Pair(s, p) for s, p in zip(suite, predictions, strict=True)])
    report = tmp_path / "run.json"
    report.write_text(json.dumps(build_report("agent", suite, predictions, metrics, DEFAULT_SUITE)), encoding="utf-8")
    assert main(["rescore", str(report)]) == 0
    rescored = tmp_path / "run_temporal.json"
    assert rescored.exists()
    data = json.loads(rescored.read_text(encoding="utf-8"))
    assert data["options"]["rescored_from"] == "run.json"
    assert next(p for p in data["predictions"] if p["scenario_id"] == scenario.scenario_id)["suspects"] == []
    assert "Top-1 change accuracy" in summarize_reports([rescored])


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
