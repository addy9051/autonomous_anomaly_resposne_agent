"""Change attribution: the temporal rule, validation of the model's answer, and failure handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from agents.diagnosis.attribution import (
    Attribution,
    apply_temporal_rule,
    build_attribution_prompt,
    candidate_changes,
    decide_attribution,
    parse_attribution,
    rule_out_acausal_changes,
    symptom_onset,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def _record(kind: str, minutes_before: int, text: str) -> dict[str, Any]:
    when = (NOW - timedelta(minutes=minutes_before)).isoformat()
    return {
        "signal_type": kind,
        "event_time": when,
        "observed_at": when,
        "description": text,
        "metric_name": text,
        "query_or_ref": text,
    }


def _timeline() -> list[dict[str, Any]]:
    """Sorted by time, as the evidence collector returns it: E1 old change, E2 error onset, E3 late change."""
    return [
        _record("deploy_event", 60, "old deploy of payments"),
        _record("metric", 30, "error_rate 8.0"),
        _record("metric", 25, "latency_p99 2400"),
        _record("deploy_event", 10, "late config change on frontend"),
    ]


def test_onset_is_the_earliest_symptom_and_ignores_changes() -> None:
    onset = symptom_onset(_timeline())
    assert onset == NOW - timedelta(minutes=30)
    assert symptom_onset([_record("deploy_event", 5, "only a change")]) is None


def test_only_changes_after_the_symptom_started_are_ruled_out() -> None:
    ruled_out = rule_out_acausal_changes(_timeline())
    assert list(ruled_out) == ["E4"]
    assert "after the earliest symptom" in ruled_out["E4"]


def test_nothing_is_ruled_out_without_any_symptom_evidence() -> None:
    assert rule_out_acausal_changes([_record("deploy_event", 5, "a change")]) == {}
    assert rule_out_acausal_changes([]) == {}


def test_naive_timestamps_are_treated_as_utc() -> None:
    timeline = _timeline()
    timeline[3]["event_time"] = (NOW - timedelta(minutes=10)).replace(tzinfo=None).isoformat()
    assert list(rule_out_acausal_changes(timeline)) == ["E4"]


def test_candidates_are_only_change_records() -> None:
    assert list(candidate_changes(_timeline())) == ["E1", "E4"]


def test_a_valid_change_answer_is_accepted_with_filtered_support() -> None:
    answer = (
        '```json\n{"decision": "change", "evidence_id": "e1", "category": "Application", "support": ["E2", "E99"]}\n```'
    )
    parsed = parse_attribution(answer, _timeline())
    assert parsed == Attribution("change", "E1", "application", ("E2",), raw=answer[:300])


def test_naming_a_signal_or_an_unknown_id_as_the_change_is_refused() -> None:
    for chosen in ("E2", "E99", ""):
        parsed = parse_attribution(f'{{"decision": "change", "evidence_id": "{chosen}"}}', _timeline())
        assert parsed.decision == "cannot_tell"
        assert parsed.change_id is None


def test_none_and_cannot_tell_answers() -> None:
    none = parse_attribution('{"decision": "none", "category": "external", "support": ["E2"]}', _timeline())
    assert (none.decision, none.change_id, none.category, none.support) == ("none", None, "external", ("E2",))
    unsure = parse_attribution('{"decision": "cannot_tell", "missing": "telemetry"}', _timeline())
    assert (unsure.decision, unsure.reason) == ("cannot_tell", "telemetry")


@pytest.mark.parametrize(
    "answer",
    ["", "no json here", "{not json}", '{"decision": "maybe"}', '["change"]', '{"decision": 5}'],
)
def test_malformed_answers_become_cannot_tell(answer: str) -> None:
    assert parse_attribution(answer, _timeline()).decision == "cannot_tell"


def test_an_unknown_category_is_dropped_not_trusted() -> None:
    parsed = parse_attribution('{"decision": "none", "category": "gremlins"}', _timeline())
    assert parsed.decision == "none"
    assert parsed.category is None


def test_the_prompt_tags_changes_and_renders_every_brace() -> None:
    prompt = build_attribution_prompt("checkout errors elevated", _timeline())
    assert "E1 [CHANGE]" in prompt
    assert "E2 [SIGNAL]" in prompt
    assert "Alert: checkout errors elevated" in prompt
    assert "{{" not in prompt and '"decision": "change"' in prompt


class _FakeLLM:
    def __init__(self, content: str | Exception) -> None:
        self.content = content
        self.calls = 0

    async def ainvoke(self, _messages: object) -> Any:  # noqa: ANN401
        self.calls += 1
        if isinstance(self.content, Exception):
            raise self.content
        return SimpleNamespace(content=self.content)


async def test_decide_attribution_asks_once_and_parses() -> None:
    llm = _FakeLLM('{"decision": "change", "evidence_id": "E1", "category": "application"}')
    result = await decide_attribution("alert", _timeline(), llm=llm)
    assert (result.decision, result.change_id, llm.calls) == ("change", "E1", 1)


async def test_a_model_failure_is_cannot_tell_not_an_exception() -> None:
    result = await decide_attribution("alert", _timeline(), llm=_FakeLLM(RuntimeError("endpoint down")))
    assert result.decision == "cannot_tell"
    assert "RuntimeError" in result.reason


def test_temporal_rule_turns_an_acausal_change_into_none() -> None:
    late = Attribution("change", "E4", "application", ("E2",))
    after = apply_temporal_rule(late, _timeline())
    assert (after.decision, after.change_id, after.category) == ("none", None, "application")
    assert "E4 ruled out" in after.reason

    early = Attribution("change", "E1", "application")
    assert apply_temporal_rule(early, _timeline()) is early
    unsure = Attribution("cannot_tell")
    assert apply_temporal_rule(unsure, _timeline()) is unsure
