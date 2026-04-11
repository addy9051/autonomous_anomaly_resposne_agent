"""
LLM Evaluation Suite — Golden Dataset Tests.

Validates RAG retrieval quality against a curated set of 20 anomaly scenarios.
Tests run in two modes:
  - Offline: Uses synthetic runbook stubs (always available)
  - Live: Uses real HybridSearchService against Supabase pgvector (when SUPABASE_DSN is set)

Architecture Reference: Phase 05 — Testing (LLM Evals)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agents.diagnosis.graph import _get_synthetic_runbooks


# ─── Load Golden Dataset ─────────────────────────────────────────

GOLDEN_DATASET_PATH = Path(__file__).parent / "golden_dataset.json"

with open(GOLDEN_DATASET_PATH) as f:
    GOLDEN_DATASET: list[dict] = json.load(f)


# ─── Offline Tests (Synthetic Runbooks) ──────────────────────────


class TestSyntheticRAGRecall:
    """
    Validate that the synthetic runbook stubs return semantically correct
    runbooks for each anomaly type. These tests are always runnable.
    """

    @pytest.mark.parametrize(
        "case",
        [c for c in GOLDEN_DATASET if c["anomaly_type"] == "latency_spike"],
        ids=[c["id"] for c in GOLDEN_DATASET if c["anomaly_type"] == "latency_spike"],
    )
    def test_latency_spike_recall(self, case: dict) -> None:
        """Latency spike anomalies should retrieve latency-related runbooks."""
        runbooks = _get_synthetic_runbooks(case["anomaly_type"], case["services"])
        retrieved_ids = [r["runbook_id"] for r in runbooks]

        # At least one expected runbook should appear in results
        expected_ids = case["expected_runbook_ids"]
        matches = [rid for rid in expected_ids if rid in retrieved_ids]
        assert len(matches) > 0, (
            f"[{case['id']}] Expected one of {expected_ids} in {retrieved_ids}"
        )

    @pytest.mark.parametrize(
        "case",
        [c for c in GOLDEN_DATASET if c["anomaly_type"] == "error_rate"],
        ids=[c["id"] for c in GOLDEN_DATASET if c["anomaly_type"] == "error_rate"],
    )
    def test_error_rate_recall(self, case: dict) -> None:
        """Error rate anomalies should retrieve error investigation runbooks."""
        runbooks = _get_synthetic_runbooks(case["anomaly_type"], case["services"])
        retrieved_ids = [r["runbook_id"] for r in runbooks]

        expected_ids = case["expected_runbook_ids"]
        matches = [rid for rid in expected_ids if rid in retrieved_ids]
        assert len(matches) > 0, (
            f"[{case['id']}] Expected one of {expected_ids} in {retrieved_ids}"
        )

    @pytest.mark.parametrize(
        "case",
        [c for c in GOLDEN_DATASET if c["anomaly_type"] == "fraud_signal"],
        ids=[c["id"] for c in GOLDEN_DATASET if c["anomaly_type"] == "fraud_signal"],
    )
    def test_fraud_signal_recall(self, case: dict) -> None:
        """Fraud signal anomalies should retrieve fraud-related runbooks."""
        runbooks = _get_synthetic_runbooks(case["anomaly_type"], case["services"])
        retrieved_ids = [r["runbook_id"] for r in runbooks]

        expected_ids = case["expected_runbook_ids"]
        matches = [rid for rid in expected_ids if rid in retrieved_ids]
        assert len(matches) > 0, (
            f"[{case['id']}] Expected one of {expected_ids} in {retrieved_ids}"
        )

    def test_unknown_anomaly_fallback(self) -> None:
        """Unknown anomaly types should fall back to general incident response."""
        case = next(c for c in GOLDEN_DATASET if c["id"] == "EVAL-015")
        runbooks = _get_synthetic_runbooks(case["anomaly_type"], case["services"])
        retrieved_ids = [r["runbook_id"] for r in runbooks]
        assert "runbook://general/incident-response" in retrieved_ids


class TestRetrievalQuality:
    """Validate retrieval quality metrics across the golden dataset."""

    def test_all_cases_return_results(self) -> None:
        """Every golden case should return at least 1 runbook."""
        for case in GOLDEN_DATASET:
            runbooks = _get_synthetic_runbooks(case["anomaly_type"], case["services"])
            assert len(runbooks) > 0, (
                f"[{case['id']}] No runbooks returned for {case['anomaly_type']}"
            )

    def test_similarity_scores_valid_range(self) -> None:
        """All similarity scores should be between 0 and 1."""
        for case in GOLDEN_DATASET:
            runbooks = _get_synthetic_runbooks(case["anomaly_type"], case["services"])
            for rb in runbooks:
                score = rb["similarity_score"]
                assert 0.0 <= score <= 1.0, (
                    f"[{case['id']}] Score {score} out of range for {rb['runbook_id']}"
                )

    def test_top_result_high_confidence(self) -> None:
        """For known anomaly types, the top result should have confidence > 0.80."""
        known_types = {"latency_spike", "error_rate", "fraud_signal"}
        for case in GOLDEN_DATASET:
            if case["anomaly_type"] in known_types:
                runbooks = _get_synthetic_runbooks(case["anomaly_type"], case["services"])
                top_score = runbooks[0]["similarity_score"]
                assert top_score > 0.80, (
                    f"[{case['id']}] Top score {top_score} is below 0.80 threshold"
                )

    def test_overall_recall_at_k(self) -> None:
        """
        Aggregate recall@K across all golden dataset cases.
        At least 80% of cases should have their expected runbook in the top results.
        """
        hits = 0
        total = 0
        for case in GOLDEN_DATASET:
            runbooks = _get_synthetic_runbooks(case["anomaly_type"], case["services"])
            retrieved_ids = [r["runbook_id"] for r in runbooks]
            expected_ids = case["expected_runbook_ids"]

            if any(eid in retrieved_ids for eid in expected_ids):
                hits += 1
            total += 1

        recall = hits / total if total > 0 else 0
        assert recall >= 0.80, (
            f"Overall recall@K = {recall:.2%} is below the 80% threshold "
            f"({hits}/{total} cases matched)"
        )

    def test_golden_dataset_integrity(self) -> None:
        """Verify the golden dataset file is well-formed."""
        assert len(GOLDEN_DATASET) == 20, f"Expected 20 cases, found {len(GOLDEN_DATASET)}"
        required_fields = {
            "id", "description", "anomaly_type", "services",
            "metrics", "expected_root_cause_category",
            "expected_runbook_ids", "expected_actions_contain",
        }
        for case in GOLDEN_DATASET:
            missing = required_fields - set(case.keys())
            assert not missing, f"[{case.get('id', '?')}] Missing fields: {missing}"
