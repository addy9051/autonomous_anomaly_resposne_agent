"""Measure supervisor expert selection against provisional, web-researched labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_LABELS = DATA_DIR / "specialist_routing_labels.json"
DEFAULT_PREDICTIONS = Path(__file__).parent / "reports" / "specialist_routing_predictions.json"
EXPERTS = ("database_expert", "network_expert", "security_expert", "application_expert")
DEFAULT_EXPERT = "application_expert"


def _effective_prediction(row: dict[str, Any]) -> set[str]:
    if row.get("error"):
        raise ValueError(f"Prediction failed for {row.get('case_id')}: {row['error']}")

    candidates = row.get("predicted_experts")
    if row.get("parse_error") or not isinstance(candidates, list):
        return {DEFAULT_EXPERT}
    selected = {expert for expert in candidates if expert in EXPERTS}
    # Match supervisor_node: if parsing/validation leaves no dispatchable role,
    # it dispatches the application expert as the fallback.
    return selected or {DEFAULT_EXPERT}


def evaluate_routing(labels: dict[str, Any], predictions: dict[str, Any]) -> dict[str, Any]:
    label_rows = labels["cases"]
    prediction_rows = predictions["predictions"]
    label_ids = [row["case_id"] for row in label_rows]
    prediction_ids = [row["case_id"] for row in prediction_rows]
    if len(set(label_ids)) != len(label_ids):
        raise ValueError("Routing labels contain duplicate case IDs.")
    if len(set(prediction_ids)) != len(prediction_ids):
        raise ValueError("Routing predictions contain duplicate case IDs.")
    if set(label_ids) != set(prediction_ids):
        missing = sorted(set(label_ids) - set(prediction_ids))
        extra = sorted(set(prediction_ids) - set(label_ids))
        raise ValueError(f"Label/prediction case IDs differ; missing={missing}, extra={extra}.")

    label_by_id = {row["case_id"]: row for row in label_rows}
    prediction_by_id = {row["case_id"]: row for row in prediction_rows}
    counts = {expert: {"tp": 0, "fp": 0, "fn": 0, "gold": 0, "predicted": 0} for expert in EXPERTS}
    exact_cases = 0
    false_dispatch_cases = 0
    missed_cases = 0
    none_cases = 0
    none_correct = 0
    fallback_cases: list[str] = []
    total_missed = 0
    total_required = 0
    total_unnecessary = 0
    total_dispatched = 0
    per_case: list[dict[str, Any]] = []

    for case_id in label_ids:
        gold = set(label_by_id[case_id]["required_experts"])
        invalid_gold = gold - set(EXPERTS)
        if invalid_gold:
            raise ValueError(f"Unknown gold expert(s) for {case_id}: {sorted(invalid_gold)}")
        predicted = _effective_prediction(prediction_by_id[case_id])
        if prediction_by_id[case_id].get("fallback_reason"):
            fallback_cases.append(case_id)
        if predicted == gold:
            exact_cases += 1
        false_positives = predicted - gold
        false_negatives = gold - predicted
        if false_positives:
            false_dispatch_cases += 1
        if false_negatives:
            missed_cases += 1
        if not gold:
            none_cases += 1
            none_correct += int(not predicted)
        total_required += len(gold)
        total_dispatched += len(predicted)
        total_missed += len(false_negatives)
        total_unnecessary += len(false_positives)
        for expert in EXPERTS:
            in_gold = expert in gold
            in_prediction = expert in predicted
            counts[expert]["gold"] += int(in_gold)
            counts[expert]["predicted"] += int(in_prediction)
            counts[expert]["tp"] += int(in_gold and in_prediction)
            counts[expert]["fp"] += int(not in_gold and in_prediction)
            counts[expert]["fn"] += int(in_gold and not in_prediction)
        per_case.append(
            {
                "case_id": case_id,
                "required_experts": sorted(gold),
                "dispatched_experts": sorted(predicted),
                "missed_required_experts": sorted(false_negatives),
                "unnecessary_experts": sorted(false_positives),
                "exact_match": predicted == gold,
                "fallback_reason": prediction_by_id[case_id].get("fallback_reason"),
            }
        )

    per_expert: dict[str, Any] = {}
    for expert, c in counts.items():
        precision = c["tp"] / c["predicted"] if c["predicted"] else 0.0
        recall = c["tp"] / c["gold"] if c["gold"] else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if precision + recall else 0.0
        per_expert[expert] = c | {"precision": precision, "recall": recall, "f1": f1}

    micro_tp = sum(c["tp"] for c in counts.values())
    micro_precision = micro_tp / total_dispatched if total_dispatched else 0.0
    micro_recall = micro_tp / total_required if total_required else 0.0
    micro_f1 = (
        2 * micro_precision * micro_recall / (micro_precision + micro_recall) if micro_precision + micro_recall else 0.0
    )
    return {
        "label_set": labels.get("label_set", "unspecified"),
        "label_reviewed_by_sre": False,
        "model": predictions.get("model", "unspecified"),
        "n_cases": len(label_rows),
        "per_expert": per_expert,
        "micro": {
            "tp": micro_tp,
            "fp": total_unnecessary,
            "fn": total_missed,
            "precision": micro_precision,
            "recall": micro_recall,
            "f1": micro_f1,
        },
        "routing": {
            "exact_set_match_cases": exact_cases,
            "exact_set_match_rate": exact_cases / len(label_rows) if label_rows else 0.0,
            "missed_required_expert_assignments": total_missed,
            "total_required_expert_assignments": total_required,
            "missed_required_expert_rate": total_missed / total_required if total_required else 0.0,
            "cases_with_a_missed_required_expert": missed_cases,
            "missed_case_rate": missed_cases / len(label_rows) if label_rows else 0.0,
            "unnecessary_dispatches": total_unnecessary,
            "total_dispatches": total_dispatched,
            "unnecessary_dispatch_rate_per_dispatch": total_unnecessary / total_dispatched if total_dispatched else 0.0,
            "cases_with_unnecessary_dispatch": false_dispatch_cases,
            "unnecessary_dispatch_case_rate": false_dispatch_cases / len(label_rows) if label_rows else 0.0,
            "none_required_cases": none_cases,
            "none_required_cases_correctly_left_undispatched": none_correct,
            "supervisor_fallback_cases": fallback_cases,
            "supervisor_fallback_count": len(fallback_cases),
        },
        "per_case": per_case,
        "limitations": [
            "Routing labels are web-researched provisional judgments, not SRE-adjudicated ground truth.",
            (
                "The supervisor falls back to application_expert on malformed, empty, or non-dispatchable "
                "selections; metrics include this actual fallback."
            ),
            (
                "The model inputs are adapted from golden case issue/evidence fields and do not reproduce "
                "every live AnomalyEvent field."
            ),
            "This small synthetic set supports diagnostic iteration, not a production promotion claim.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    report = evaluate_routing(labels, predictions)
    rendered = json.dumps(report, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
