"""Layer 3 evals: rubric, judge plumbing, agreement metrics and the 80% promotion gate.

All judge calls here use scripted fakes and synthetic labels; no network and no real human data.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from evals.agreement import (
    aggregate_human_labels,
    compute_agreement,
    evaluate_gate,
    quadratic_weighted_kappa,
)
from evals.judge import LLMJudge, build_user_prompt, judge_fingerprint, parse_response
from evals.rubric import DIMENSION_KEYS, RubricError, overall_score, validate_scores
from evals.run_judge_gate import load_cases, run, split_cases

CASES_PATH = Path(__file__).parents[2] / "evals" / "data" / "golden_cases.json"


def scores(**over: int) -> dict[str, int]:
    return {**dict.fromkeys(DIMENSION_KEYS, 4), **over}


class TestRubric:
    def test_overall_is_weighted_mean(self) -> None:
        assert overall_score(scores()) == 4
        assert overall_score(dict.fromkeys(DIMENSION_KEYS, 5)) == 5

    def test_unsafe_action_caps_overall(self) -> None:
        s = {k: 5 for k in DIMENSION_KEYS} | {"action_safety_and_fit": 1}
        assert overall_score(s) == 2

    @pytest.mark.parametrize("bad", [0, 6, 3.5, "4", True])
    def test_rejects_out_of_range_or_non_integer(self, bad: object) -> None:
        with pytest.raises(RubricError):
            validate_scores(scores() | {"diagnostic_accuracy": bad})

    def test_rejects_missing_dimension(self) -> None:
        s = scores()
        s.pop("evidence_grounding")
        with pytest.raises(RubricError):
            validate_scores(s)


class TestJudgePlumbing:
    CASE = {"id": "c1", "issue": "x", "agent_output": {"root_cause": "r"}}

    def test_parses_fenced_json(self) -> None:
        raw = "```json\n" + json.dumps(scores() | {"justification": "ok"}) + "\n```"
        v = parse_response("c1", raw)
        assert v.overall == 4 and v.justification == "ok"

    def test_non_json_raises(self) -> None:
        with pytest.raises(RubricError):
            parse_response("c1", "I think it is good")

    def test_prompt_contains_rubric_and_never_human_labels(self) -> None:
        prompt = build_user_prompt({**self.CASE, "human_scores": {"secret": 1}})
        assert "diagnostic_accuracy" in prompt and "secret" not in prompt

    def test_anchors_are_rendered(self) -> None:
        anchor = {**self.CASE, "id": "a1", "anchor_scores": scores()}
        assert "Calibration examples" in build_user_prompt(self.CASE, [anchor])

    async def test_judge_scores_via_injected_call(self) -> None:
        async def fake(_s: str, _u: str) -> str:
            return json.dumps(scores() | {"justification": "j"})

        verdict = await LLMJudge(fake, "m").score(self.CASE)
        assert verdict.case_id == "c1" and verdict.overall == 4

    def test_fingerprint_changes_with_model_and_anchors(self) -> None:
        a = judge_fingerprint("m1")
        assert a != judge_fingerprint("m2") and a != judge_fingerprint("m1", [{"id": "a1"}])


class TestAgreementMath:
    def test_kappa_perfect_and_worst(self) -> None:
        assert quadratic_weighted_kappa([1, 3, 5], [1, 3, 5]) == pytest.approx(1.0)
        assert quadratic_weighted_kappa([1, 5, 1, 5], [5, 1, 5, 1]) < 0

    def test_constant_judge_has_near_zero_kappa(self) -> None:
        human = [1, 2, 3, 4, 5, 4, 2, 5]
        assert abs(quadratic_weighted_kappa(human, [3] * len(human))) < 0.05

    def test_human_aggregation_takes_median_and_counts_raters(self) -> None:
        rows = [
            {"case_id": "c", "rater_id": r, "scores": scores(diagnostic_accuracy=v)}
            for r, v in zip("abc", (2, 4, 5), strict=False)
        ]
        agg = aggregate_human_labels(rows)["c"]
        assert agg["scores"]["diagnostic_accuracy"] == 4 and agg["n_raters"] == 3


def _synthetic(n: int, noise: float, seed: int = 7) -> tuple[dict, dict]:
    rng = random.Random(seed)  # noqa: S311
    human, judge = {}, {}
    for i in range(n):
        h = {k: rng.randint(1, 5) for k in DIMENSION_KEYS}
        j = {k: min(5, max(1, v + (rng.choice((-2, -1, 1, 2)) if rng.random() < noise else 0))) for k, v in h.items()}
        human[f"c{i}"] = {"scores": h, "overall": overall_score(h)}
        judge[f"c{i}"] = {"scores": j, "overall": overall_score(j)}
    return human, judge


class TestGate:
    def test_faithful_judge_passes(self) -> None:
        h, j = _synthetic(60, noise=0.0)
        gate = evaluate_gate(compute_agreement(h, j))
        assert gate.passed and gate.ready

    def test_noisy_judge_fails(self) -> None:
        h, j = _synthetic(60, noise=0.9)
        gate = evaluate_gate(compute_agreement(h, j))
        assert not gate.passed and gate.ready and gate.reasons

    def test_too_few_labels_is_not_ready_never_a_pass(self) -> None:
        h, j = _synthetic(10, noise=0.0)
        gate = evaluate_gate(compute_agreement(h, j))
        assert not gate.passed and not gate.ready

    def test_always_three_judge_fails_on_kappa_even_if_within_one_is_high(self) -> None:
        h = {f"c{i}": {"scores": dict.fromkeys(DIMENSION_KEYS, 2 + (i % 3)), "overall": 2 + (i % 3)} for i in range(60)}
        j = {k: {"scores": dict.fromkeys(DIMENSION_KEYS, 3), "overall": 3} for k in h}
        report = compute_agreement(h, j)
        assert report.within_one_agreement == 1.0
        gate = evaluate_gate(report)
        assert not gate.passed and any("kappa" in r for r in gate.reasons)

    def test_missed_unsafe_case_blocks_promotion(self) -> None:
        h, j = _synthetic(60, noise=0.0)
        unsafe = next(k for k, v in h.items() if v["scores"]["action_safety_and_fit"] <= 2)
        j[unsafe]["scores"]["action_safety_and_fit"] = 5
        j[unsafe]["overall"] = 4
        report = compute_agreement(h, j)
        assert report.unsafe_recall is not None and report.unsafe_recall < 1.0

    def test_no_overlap_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_agreement({"a": {"scores": scores(), "overall": 4}}, {"b": {"scores": scores(), "overall": 4}})


class TestRunner:
    def test_anchors_are_excluded_from_eval_set(self) -> None:
        cases = [
            {"id": "a", "split": "anchor"},
            {"id": "e1", "split": "eval"},
            {"id": "e2", "split": "eval"},
        ]
        human = {k: {"scores": scores(), "overall": 4} for k in ("a", "e1")}
        anchors, eval_cases = split_cases(cases, human)
        assert [a["id"] for a in anchors] == ["a"] and [c["id"] for c in eval_cases] == ["e1"]

    async def test_shipped_dataset_with_no_labels_is_not_ready(self, tmp_path: Path) -> None:
        import argparse

        empty = tmp_path / "labels.jsonl"
        empty.write_text("")
        scores_file = tmp_path / "s.json"
        scores_file.write_text(json.dumps({"fingerprint": "x", "scores": {}}))
        args = argparse.Namespace(
            cases=str(CASES_PATH),
            labels=str(empty),
            judge_scores=str(scores_file),
            model="m",
            threshold=0.8,
            min_cases=30,
            out=None,
        )
        assert await run(args) == 2

    def test_golden_cases_are_well_formed(self) -> None:
        cases = load_cases(CASES_PATH)
        assert len({c["id"] for c in cases}) == len(cases)
        for c in cases:
            assert {"id", "split", "issue", "agent_output", "spec", "expect"} <= set(c)
