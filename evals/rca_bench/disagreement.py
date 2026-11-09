"""Offline test: keep the baseline attribution, cap confidence where a second, narrow question disagrees.

Pairs run k of the baseline (temporal rule applied) with run k of the ``decompose`` mode (temporal rule applied) for
the same suite. The two runs are independent generations, so this approximates the live harness variant and is not
identical to it. Needs the reports that ``run_attribution_experiment.ps1`` writes. No model calls.

    python -m evals.rca_bench.disagreement --reports evals/reports --runs 3
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import NamedTuple

from evals.rca_bench.metrics import Pair, compute_metrics
from evals.rca_bench.runner import load_suite
from evals.rca_bench.schema import Prediction, Scenario

DISAGREEMENT_CAP = 0.4
CULPRIT_CLASSES = ("bad_deploy", "bad_deploy_decoy_later", "config_change", "resource_limit_change")


def _top_group(prediction: Prediction) -> frozenset[str]:
    if prediction.abstained or not prediction.suspects:
        return frozenset()
    top = max(s.score for s in prediction.suspects)
    return frozenset(s.change_id for s in prediction.suspects if s.score == top)


def _load(path: Path) -> tuple[Path, list[Prediction]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    return Path(report["suite"]["path"]), [Prediction.model_validate(p) for p in report["predictions"]]


class Row(NamedTuple):
    top1: float
    confident_errors: float
    ece: float
    brier: float
    culprit_classes: float


def _value(x: float | None) -> float:
    return float("nan") if x is None else x


def _score(suite: dict[str, Scenario], predictions: list[Prediction]) -> Row:
    m = compute_metrics([Pair(suite[p.scenario_id], p) for p in predictions])
    culprit = statistics.mean(float(m.by_fault_class[c]["accuracy"]) for c in CULPRIT_CLASSES)
    return Row(_value(m.top1_change_accuracy), _value(m.confident_error_rate), _value(m.ece), _value(m.brier), culprit)


def evaluate(reports: Path, suite_name: str, runs: int) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {"temporal": [], "temporal + disagreement cap": []}
    disagreement_counts: list[int] = []
    for k in range(1, runs + 1):
        suite_path, base = _load(reports / f"exp_{suite_name}_baseline_run{k}_temporal.json")
        _, narrow = _load(reports / f"att_{suite_name}_decompose_run{k}_temporal.json")
        scenarios = {s.scenario_id: s for s in load_suite(suite_path)}
        narrow_by_id = {p.scenario_id: p for p in narrow}
        capped, disagreements = [], 0
        for p in base:
            if _top_group(p) != _top_group(narrow_by_id[p.scenario_id]):
                disagreements += 1
                p = p.model_copy(update={"confidence": min(p.confidence, DISAGREEMENT_CAP)})
            capped.append(p)
        out["temporal"].append(_score(scenarios, base))
        out["temporal + disagreement cap"].append(_score(scenarios, capped))
        disagreement_counts.append(disagreements)
    print(f"disagreements per run ({suite_name}): {statistics.mean(disagreement_counts):.1f} of 40")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", type=Path, default=Path("evals/reports"))
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    for suite_name in ("dev", "holdout"):
        results = evaluate(args.reports, suite_name, args.runs)
        print(f"##### {suite_name} #####")
        for name, rows in results.items():
            mean = Row(*(statistics.mean(r[i] for r in rows) for i in range(len(Row._fields))))
            print(
                f"{name:30s} top1={mean.top1:.3f} confident_errors={mean.confident_errors:.3f} "
                f"ece={mean.ece:.3f} brier={mean.brier:.3f} culprit_classes={mean.culprit_classes:.3f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
