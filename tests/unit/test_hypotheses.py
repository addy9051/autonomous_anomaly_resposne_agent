"""Tests for evidence-grounded hypothesis modelling."""

from __future__ import annotations

from agents.diagnosis.hypotheses import (
    build_evidence_index,
    cap_overall_confidence,
    ground_hypotheses,
)

REAL = {"source": "prometheus", "metric_name": "p99", "value": 900, "simulated": False}
REAL2 = {"source": "github", "metric_name": "deployment", "value": 1, "simulated": False}
SIM = {"source": "simulator", "metric_name": "p99", "value": 1, "simulated": True}


def test_index_marks_simulated() -> None:
    out = build_evidence_index([REAL, SIM])
    assert "E1:" in out and "E2:" in out and "[SIMULATED]" in out
    assert build_evidence_index([]) == "No evidence collected."


def test_invalid_citations_are_dropped() -> None:
    hs = ground_hypotheses(
        [{"hypothesis": "bad deploy", "supporting_evidence_ids": ["E1", "E9", "x", "E0"], "confidence_band": "high"}],
        [REAL, REAL2],
        [],
    )
    assert len(hs[0].supporting_evidence) == 1
    assert hs[0].confidence_band == "medium"  # single real source cannot be "high"


def test_no_support_forces_low_and_missing_note() -> None:
    hs = ground_hypotheses([{"hypothesis": "db", "confidence_band": "high"}], [REAL], ["splunk:svc:*"])
    assert hs[0].confidence_band == "low"
    assert any("no authoritative" in m for m in hs[0].missing_evidence)
    assert any("splunk" in m for m in hs[0].missing_evidence)


def test_simulated_support_is_not_authoritative() -> None:
    hs = ground_hypotheses(
        [{"hypothesis": "x", "supporting_evidence_ids": ["E1"], "confidence_band": "high"}], [SIM], []
    )
    assert hs[0].confidence_band == "low"


def test_contradiction_caps_and_orders() -> None:
    hs = ground_hypotheses(
        [
            {"hypothesis": "weak", "supporting_evidence_ids": ["E1"]},
            {
                "hypothesis": "strong",
                "supporting_evidence_ids": ["E1", "E2"],
                "contradicting_evidence_ids": [],
                "confidence_band": "high",
            },
        ],
        [REAL, REAL2],
        [],
    )
    assert hs[0].hypothesis == "strong" and hs[0].confidence_band == "high"
    contra = ground_hypotheses(
        [
            {
                "hypothesis": "c",
                "supporting_evidence_ids": ["E1", "E2"],
                "contradicting_evidence_ids": ["E2"],
                "confidence_band": "high",
            }
        ],
        [REAL, REAL2],
        [],
    )
    assert contra[0].confidence_band == "medium"
    assert contra[0].contradicting_evidence[0].is_contradictory


def test_overall_confidence_capped() -> None:
    assert cap_overall_confidence(0.95, "high", [], [REAL]) == (0.4, "low")
    assert cap_overall_confidence(0.95, "high", [], [SIM]) == (0.4, "low")
    strong = ground_hypotheses(
        [{"hypothesis": "s", "supporting_evidence_ids": ["E1", "E2"], "confidence_band": "high"}],
        [REAL, REAL2],
        [],
    )
    assert cap_overall_confidence(0.9, "high", strong, [REAL, REAL2]) == (0.9, "high")
