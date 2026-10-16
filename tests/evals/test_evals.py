"""
Golden dataset integrity.

The former offline "recall" tests exercised a hard-coded runbook dictionary rather than retrieval, so they
proved nothing about the real RAG path and were removed. Retrieval quality must be measured against the live
knowledge base (per-agent collections + policy) once it is ingested inside the VPC.
"""

from __future__ import annotations

import json
from pathlib import Path

GOLDEN_DATASET_PATH = Path(__file__).parent / "golden_dataset.json"

with open(GOLDEN_DATASET_PATH) as f:
    GOLDEN_DATASET: list[dict] = json.load(f)


def test_golden_dataset_integrity() -> None:
    assert len(GOLDEN_DATASET) == 20, f"Expected 20 cases, found {len(GOLDEN_DATASET)}"
    required_fields = {
        "id",
        "description",
        "anomaly_type",
        "services",
        "metrics",
        "expected_root_cause_category",
        "expected_runbook_ids",
        "expected_actions_contain",
    }
    for case in GOLDEN_DATASET:
        missing = required_fields - set(case.keys())
        assert not missing, f"[{case.get('id', '?')}] Missing fields: {missing}"
