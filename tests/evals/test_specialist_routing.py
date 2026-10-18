"""Tests for multi-label specialist-routing metrics and production fallbacks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.measure_specialist_routing import DEFAULT_LABELS, evaluate_routing
from evals.run_specialist_routing import _parse_dispatched_experts, build_routing_event


def _labels() -> dict:
    return {
        "label_set": "web_researched_provisional",
        "cases": [
            {"case_id": "multi", "required_experts": ["application_expert", "network_expert"]},
            {"case_id": "none", "required_experts": []},
        ],
    }


def test_evaluates_per_expert_and_dispatch_errors_with_default_fallback() -> None:
    predictions = {
        "model": "test-model",
        "predictions": [
            {"case_id": "multi", "predicted_experts": ["application_expert"]},
            {"case_id": "none", "predicted_experts": [], "parse_error": None},
        ],
    }

    result = evaluate_routing(_labels(), predictions)

    assert result["per_expert"]["application_expert"]["tp"] == 1
    assert result["per_expert"]["application_expert"]["fp"] == 1
    assert result["per_expert"]["application_expert"]["precision"] == 0.5
    assert result["per_expert"]["network_expert"]["fn"] == 1
    assert result["routing"]["missed_required_expert_rate"] == 0.5
    assert result["routing"]["unnecessary_dispatches"] == 1
    assert result["routing"]["none_required_cases_correctly_left_undispatched"] == 0
    assert result["micro"]["tp"] == 1
    assert result["micro"]["fp"] == 1
    assert result["micro"]["fn"] == 1


@pytest.mark.parametrize(
    "prediction",
    [
        {"case_id": "none", "predicted_experts": [], "parse_error": "invalid_json"},
        {"case_id": "none", "predicted_experts": ["unknown_expert"]},
        {"case_id": "none", "predicted_experts": None},
    ],
)
def test_unparseable_or_empty_selection_counts_actual_application_fallback(prediction: dict) -> None:
    result = evaluate_routing(
        {"cases": [{"case_id": "none", "required_experts": []}]},
        {"predictions": [prediction]},
    )

    assert result["per_case"][0]["dispatched_experts"] == ["application_expert"]
    assert result["routing"]["unnecessary_dispatches"] == 1


def test_requires_same_unique_case_ids() -> None:
    labels = {"cases": [{"case_id": "case", "required_experts": []}]}
    predictions = {"predictions": [{"case_id": "other", "predicted_experts": []}]}

    with pytest.raises(ValueError, match="case IDs differ"):
        evaluate_routing(labels, predictions)


def test_event_adapter_uses_issue_and_evidence_but_not_agent_output() -> None:
    event = build_routing_event(
        {
            "id": "test-case",
            "issue": "orders-db connections saturated",
            "evidence": [{"metric": "cpu_percent", "value": 95, "source": "prometheus"}],
            "agent_output": {"root_cause": "Do not use this as routing input."},
        }
    )

    assert event.event_id == "test-case"
    assert event.affected_services == ["orders-db"]
    assert event.metrics_snapshot.cpu_percent == 95
    assert "Do not use this as routing input." not in str(event.model_dump())


def test_supervisor_response_parser_matches_application_fallback() -> None:
    assert _parse_dispatched_experts('```json\n["database_expert"]\n```') == (["database_expert"], None)
    assert _parse_dispatched_experts("not JSON") == (["application_expert"], "invalid_json")
    assert _parse_dispatched_experts('```json\n[]\n```') == (
        ["application_expert"],
        "no_dispatchable_expert",
    )


def test_researched_labels_cover_golden_cases_without_label_leakage() -> None:
    labels = json.loads(DEFAULT_LABELS.read_text(encoding="utf-8"))
    golden_cases = json.loads(
        (Path(__file__).parents[2] / "evals" / "data" / "golden_cases.json").read_text(encoding="utf-8")
    )["cases"]
    case_ids = {case["id"] for case in golden_cases}

    assert labels["label_set"] == "web_researched_provisional"
    assert labels["label_policy"]["input_boundary"].find("do not use agent_output") >= 0
    assert {row["case_id"] for row in labels["cases"]} == case_ids
    assert all(
        source in labels["sources"]
        for row in labels["cases"]
        for source in row.get("sources", [])
    )
