"""Tests for reproducible specialist evaluation inputs and independent safety checks."""

from __future__ import annotations

import pytest

from evals.run_specialist_quality import (
    _deterministic_checks,
    _normalize_unsupported_claims,
    _render_case_context,
    _validate_score,
)


def test_frozen_context_uses_only_issue_and_evidence() -> None:
    case = {
        "id": "case-1",
        "issue": "orders-db connection saturation",
        "evidence": [{"id": "E1", "metric": "active_connections", "value": 80, "source": "prometheus"}],
        "agent_output": {"root_cause": "This must not be included."},
    }

    timeline, context = _render_case_context(case)

    assert timeline[0]["tags"]["golden_evidence_id"] == "E1"
    assert "orders-db connection saturation" in context
    assert "active_connections" in context
    assert "This must not be included" not in context


def test_model_scores_cannot_hide_deterministic_action_and_citation_failures() -> None:
    case = {
        "id": "case-2",
        "evidence": [{"id": "E1"}],
        "spec": {"allowed_actions": ["scale_replicas"]},
    }
    diagnosis = {
        "root_cause": "The metric E99 indicates a database issue.",
        "decision_summary": "See E99.",
        "recommended_actions": [{"action": "failover_db", "tier": 1, "params": {}}],
    }

    checks = _deterministic_checks(diagnosis, case)

    assert {item["code"] for item in checks["action_violations"]} == {
        "TIER_MISMATCH",
        "ACTION_UNEXPECTED",
    }
    assert checks["citation_violations"][0]["code"] == "CITATION_UNKNOWN"
    assert checks["blocking_guardrail_findings"] == []


def test_evaluation_runs_real_action_policy_in_dry_run_without_execution() -> None:
    checks = _deterministic_checks(
        {"recommended_actions": [{"action": "unknown-dangerous-action", "tier": 1, "params": {}}]},
        {"id": "case-policy", "evidence": [], "spec": {}},
        {"severity": "high", "affected_services": ["checkout"]},
    )

    assert checks["production_policy_checks"][0]["decision"] == "denied"
    assert checks["production_policy_checks"][0]["dry_run"] is True
    assert "no registered workflow" in checks["production_policy_checks"][0]["reason"]


def test_score_schema_rejects_boolean_and_out_of_range_values() -> None:
    with pytest.raises(ValueError, match="integer from 1 to 5"):
        _validate_score(True, "relevance")
    with pytest.raises(ValueError, match="integer from 1 to 5"):
        _validate_score(6, "relevance")
    assert _validate_score(None, "uses_relevant_findings", nullable=True) is None


def test_missing_unsupported_claim_list_is_not_misreported_as_empty() -> None:
    scores = {"evidence_grounding": 4}

    errors = _normalize_unsupported_claims(scores)

    assert scores["unsupported_claims"] is None
    assert errors == ["unsupported_claims missing or not an array of strings"]
