"""Create a provenance-preserving, machine-adjudicated copy of simulated labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evals.machine_reward import adjudicate_scores
from evals.rubric import DIMENSION_KEYS

DATA_DIR = Path(__file__).parent / "data"
METHOD = "deterministic_constraints_v1"
CHECKED_DIMENSIONS = (
    "action_safety_and_fit",
    "evidence_grounding",
    "calibration_and_communication",
)
UNVERIFIED_DIMENSIONS = DIMENSION_KEYS


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def adjudicate_labels(cases: list[dict[str, Any]], labels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cases_by_id = {case["id"]: case for case in cases}
    if len(cases_by_id) != len(cases):
        raise ValueError("Golden case IDs must be unique.")

    output: list[dict[str, Any]] = []
    for row in labels:
        if row.get("source") != "ai_simulated":
            raise ValueError(f"Refusing to adjudicate non-simulated row {row.get('case_id')!r}.")
        case_id = row.get("case_id")
        case = cases_by_id.get(case_id)
        if case is None:
            raise ValueError(f"Label references unknown golden case {case_id!r}.")

        result = adjudicate_scores(case, row["scores"])
        adjusted_dimensions = result.adjusted_dimensions
        before_after = ", ".join(
            f"{dimension} {change['from']}→{change['to']}" for dimension, change in adjusted_dimensions.items()
        )
        correction_text = (
            f" Machine-checked score caps: {before_after}."
            if before_after
            else " No scores contradicted a detected machine-checkable constraint."
        )
        rationale = (
            "Weak machine adjudication uses deterministic action/tier/execution rules, evidence "
            "citation/required-tool checks, and the overconfidence detector."
            f"{correction_text} No detected violation is not proof of correctness. "
            "Diagnostic accuracy remains unverified; this is not an SRE label."
        )

        adjudicated = {
            **row,
            "scores": result.scores,
            "justification": rationale,
            "source": "ai_simulated_machine_adjudicated",
            "original_source": row["source"],
            "original_simulated_scores": dict(row["scores"]),
            "original_simulated_justification": row.get("justification", ""),
            "machine_adjudication": {
                "method": METHOD,
                "constraint_checked_dimensions": list(CHECKED_DIMENSIONS),
                "fully_verified_dimensions": [],
                "unverified_dimensions": list(UNVERIFIED_DIMENSIONS),
                "adjusted_dimensions": adjusted_dimensions,
                "unverified_low_scores": list(result.unverified_low_scores),
                "deterministic_violations": list(result.checks.violations),
                "guardrail_findings": list(result.checks.findings),
                "unscored_findings": list(result.checks.unscored),
            },
        }
        output.append(adjudicated)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DATA_DIR / "golden_cases.json")
    parser.add_argument("--labels", type=Path, default=DATA_DIR / "simulated_labels.jsonl")
    parser.add_argument("--out", type=Path, default=DATA_DIR / "machine_adjudicated_labels.jsonl")
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    labels = load_jsonl(args.labels)
    adjudicated = adjudicate_labels(cases, labels)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in adjudicated), encoding="utf-8")

    changed = sum(bool(row["machine_adjudication"]["adjusted_dimensions"]) for row in adjudicated)
    unresolved = sum(bool(row["machine_adjudication"]["unscored_findings"]) for row in adjudicated)
    print(
        f"Wrote {len(adjudicated)} machine-adjudicated simulated ratings to {args.out}; "
        f"{changed} ratings adjusted, {unresolved} retain unscored findings."
    )


if __name__ == "__main__":
    main()
