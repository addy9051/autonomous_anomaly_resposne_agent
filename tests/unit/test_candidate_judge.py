"""Per-candidate judging: token log-probabilities, aggregation rules, and failure handling."""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import pytest

from agents.diagnosis.candidate_judge import (
    AMBIGUITY_MARGIN,
    Verdict,
    aggregate,
    classify_token,
    judge_candidates,
    probabilities_from_logprobs,
    verdict_from_response,
)


def _entry(token: str, **alternatives: float) -> dict[str, Any]:
    """A logprobs content entry whose top_logprobs hold the given token -> probability pairs."""
    tops = [{"token": t, "logprob": math.log(p)} for t, p in alternatives.items()]
    return {"token": token, "logprob": math.log(alternatives.get(token, 0.5)), "top_logprobs": tops}


def _v(eid: str, yes: float = 0.0, no: float = 0.0, unclear: float = 0.0) -> Verdict:
    return Verdict(eid, yes, no, unclear)


def test_classify_token_accepts_case_spacing_and_split_unclear() -> None:
    assert [classify_token(t) for t in ("YES", " yes", "Y", "NO", "No", " N", "UNCLEAR", "UN", "Unc")] == [
        "yes",
        "yes",
        "yes",
        "no",
        "no",
        "no",
        "unclear",
        "unclear",
        "unclear",
    ]
    assert [classify_token(t) for t in ("", "The", "U", "NOT", "MAYBE")] == [None] * 5


def test_probabilities_are_normalised_over_the_three_answers() -> None:
    p_yes, p_no, p_unclear, mass = probabilities_from_logprobs([_entry("YES", YES=0.6, NO=0.2, UN=0.1, The=0.1)])  # type: ignore[misc]
    assert (round(p_yes, 3), round(p_no, 3), round(p_unclear, 3)) == (0.667, 0.222, 0.111)
    assert mass == pytest.approx(0.9)


def test_variants_of_the_same_answer_are_summed() -> None:
    p_yes, p_no, _, _ = probabilities_from_logprobs([_entry("YES", YES=0.3, Yes=0.3, NO=0.4)])  # type: ignore[misc]
    assert (round(p_yes, 2), round(p_no, 2)) == (0.6, 0.4)


def test_sampled_token_is_included_even_when_missing_from_the_alternatives() -> None:
    entry = {"token": "NO", "logprob": math.log(0.9), "top_logprobs": [{"token": "YES", "logprob": math.log(0.1)}]}
    p_yes, p_no, _, _ = probabilities_from_logprobs([entry])  # type: ignore[misc]
    assert (round(p_yes, 2), round(p_no, 2)) == (0.1, 0.9)


def test_no_usable_logprobs_return_none() -> None:
    assert probabilities_from_logprobs(None) is None
    assert probabilities_from_logprobs([]) is None
    assert probabilities_from_logprobs([_entry("The", The=0.9)]) is None


def test_a_blank_first_token_is_skipped() -> None:
    p_yes, *_ = probabilities_from_logprobs([_entry(" ", Space=0.9), _entry("YES", YES=1.0)])  # type: ignore[misc]
    assert p_yes == pytest.approx(1.0)


def test_verdict_uses_logprobs_when_trustworthy() -> None:
    verdict = verdict_from_response("E3", "YES", [_entry("YES", YES=0.8, NO=0.2)])
    assert verdict.from_logprobs
    assert (round(verdict.p_yes, 2), round(verdict.p_no, 2)) == (0.8, 0.2)


def test_an_off_format_answer_is_unclear_not_a_guess() -> None:
    verdict = verdict_from_response("E3", "The change", [_entry("YES", YES=0.1, The=0.9)])
    assert (verdict.p_unclear, verdict.note) == (1.0, "answer was not YES, NO or UNCLEAR")


def test_without_logprobs_the_written_word_is_used_and_flagged() -> None:
    yes = verdict_from_response("E1", "Yes, because", None)
    no = verdict_from_response("E1", "no.", None)
    junk = verdict_from_response("E1", "I think so", None)
    assert (yes.p_yes, yes.from_logprobs) == (1.0, False)
    assert (no.p_no, no.from_logprobs) == (1.0, False)
    assert (junk.p_unclear, junk.from_logprobs) == (1.0, False)


def test_no_symptoms_means_cannot_tell_even_with_a_supported_change() -> None:
    result = aggregate([_v("E1", yes=0.95)], has_symptoms=False, missing_sources=[], fallback_confidence=0.9)
    assert (result.decision, result.confidence) == ("cannot_tell", 0.0)


def test_one_supported_change_is_named_with_its_probability_as_confidence() -> None:
    result = aggregate(
        [_v("E1", yes=0.9, no=0.1), _v("E2", no=0.95, unclear=0.05)],
        has_symptoms=True,
        missing_sources=[],
        fallback_confidence=0.9,
    )
    assert (result.decision, result.change_id, result.confidence) == ("change", "E1", 0.9)


def test_a_clear_leader_is_named_with_confidence_reduced_by_the_runner_up() -> None:
    result = aggregate(
        [_v("E1", yes=0.95, no=0.05), _v("E2", yes=0.6, no=0.4)],
        has_symptoms=True,
        missing_sources=[],
        fallback_confidence=0.9,
    )
    assert (result.decision, result.change_id) == ("change", "E1")
    assert result.confidence == pytest.approx(0.95 * 0.4, abs=1e-4)


def test_two_equally_plausible_changes_mean_cannot_tell() -> None:
    gap = AMBIGUITY_MARGIN / 2
    result = aggregate(
        [_v("E1", yes=0.9, no=0.1), _v("E2", yes=0.9 - gap, no=0.1 + gap)],
        has_symptoms=True,
        missing_sources=[],
        fallback_confidence=0.9,
    )
    assert (result.decision, result.change_id, result.confidence) == ("cannot_tell", None, 0.0)
    assert "equally plausible" in result.reason


def test_no_supported_change_and_every_candidate_judged_no_means_none_with_the_weakest_no() -> None:
    result = aggregate(
        [_v("E1", no=0.9, yes=0.1), _v("E2", no=0.7, yes=0.3)],
        has_symptoms=True,
        missing_sources=[],
        fallback_confidence=0.9,
    )
    assert (result.decision, result.change_id, result.confidence) == ("none", None, 0.7)


def test_an_unavailable_source_blocks_none_because_an_unseen_change_cannot_be_ruled_out() -> None:
    result = aggregate(
        [_v("E1", no=0.99, yes=0.01)],
        has_symptoms=True,
        missing_sources=["github:deployments"],
        fallback_confidence=0.9,
    )
    assert result.decision == "cannot_tell"


def test_an_unclear_candidate_blocks_none() -> None:
    result = aggregate(
        [_v("E1", no=0.9, yes=0.1), _v("E2", unclear=0.7, no=0.3)],
        has_symptoms=True,
        missing_sources=[],
        fallback_confidence=0.9,
    )
    assert result.decision == "cannot_tell"


def test_no_candidates_means_none_with_the_diagnosis_confidence_unless_a_source_is_missing() -> None:
    plain = aggregate([], has_symptoms=True, missing_sources=[], fallback_confidence=0.4)
    assert (plain.decision, plain.confidence) == ("none", 0.4)
    blocked = aggregate([], has_symptoms=True, missing_sources=["github:deployments"], fallback_confidence=0.4)
    assert blocked.decision == "cannot_tell"


def test_a_supported_change_wins_over_a_missing_source() -> None:
    result = aggregate([_v("E1", yes=0.9)], has_symptoms=True, missing_sources=["x"], fallback_confidence=0.5)
    assert result.decision == "change"


class _FakeJudge:
    """Answers YES for one chosen candidate and NO for the rest, recording what it was asked."""

    def __init__(self, yes_for: str | None) -> None:
        self.yes_for = yes_for
        self.prompts: list[str] = []

    async def ainvoke(self, messages: list[Any]) -> Any:  # noqa: ANN401
        prompt = messages[-1].content
        self.prompts.append(prompt)
        wants_yes = self.yes_for is not None and f"Candidate change to judge: {self.yes_for} " in prompt
        token = "YES" if wants_yes else "NO"
        entry = {"token": token, "logprob": math.log(0.9), "top_logprobs": [{"token": token, "logprob": math.log(0.9)}]}
        return SimpleNamespace(content=token, response_metadata={"logprobs": {"content": [entry]}})


async def test_judge_candidates_asks_about_each_listed_change_and_skips_excluded_ones() -> None:
    evidence = [
        {"signal_type": "custom", "metric_name": "error_rate", "event_time": "2026-01-01T00:10:00+00:00"},
        {
            "signal_type": "deploy_event",
            "metric_name": "chg-001 new pricing",
            "event_time": "2026-01-01T00:00:00+00:00",
        },
        {"signal_type": "deploy_event", "metric_name": "chg-002 config", "event_time": "2026-01-01T00:20:00+00:00"},
    ]
    judge = _FakeJudge(yes_for="E2")
    verdicts = await judge_candidates("alert", evidence, llm=judge)
    assert [(v.evidence_id, round(v.p_yes, 1)) for v in verdicts] == [("E2", 1.0), ("E3", 0.0)]
    assert len(judge.prompts) == 2

    judge = _FakeJudge(yes_for="E2")
    verdicts = await judge_candidates("alert", evidence, exclude=frozenset({"E3"}), llm=judge)
    assert [v.evidence_id for v in verdicts] == ["E2"]
    assert await judge_candidates("alert", evidence[:1], llm=judge) == []


async def test_a_failing_model_call_becomes_unclear_not_an_exception() -> None:
    class Broken:
        async def ainvoke(self, _messages: list[Any]) -> Any:  # noqa: ANN401
            raise TimeoutError("endpoint down")

    evidence = [{"signal_type": "deploy_event", "metric_name": "chg-001", "event_time": "2026-01-01T00:00:00+00:00"}]
    (verdict,) = await judge_candidates("alert", evidence, llm=Broken())
    assert (verdict.p_unclear, verdict.from_logprobs) == (1.0, False)
    assert "TimeoutError" in verdict.note
