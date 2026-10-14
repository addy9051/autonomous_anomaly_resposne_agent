"""
Judge promotion gate. Exit codes: 0 = gate passed, 1 = gate failed, 2 = not ready (too few human labels).

  python -m evals.run_judge_gate                       # call the judge live (needs OPENAI_API_KEY)
  python -m evals.run_judge_gate --judge-scores f.json # reuse cached judge scores, no network
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from evals.agreement import (
    DEFAULT_MIN_CASES,
    DEFAULT_THRESHOLD,
    aggregate_human_labels,
    compute_agreement,
    evaluate_gate,
)
from evals.judge import LLMJudge, openai_judge_fn

DATA_DIR = Path(__file__).parent / "data"


def load_cases(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["cases"]


def load_labels(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in rows]


def split_cases(
    cases: list[dict[str, Any]], human: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Anchors (few-shot, labelled) are excluded from the eval set to prevent leakage."""
    anchors = []
    for c in cases:
        if c.get("split") == "anchor" and c["id"] in human:
            anchors.append({**c, "anchor_scores": human[c["id"]]["scores"]})
    eval_cases = [c for c in cases if c.get("split", "eval") == "eval" and c["id"] in human]
    return anchors, eval_cases


async def score_cases(judge: LLMJudge, cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for case in cases:
        verdict = await judge.score(case)
        out[case["id"]] = {"scores": verdict.scores, "overall": verdict.overall, "justification": verdict.justification}
    return out


async def run(args: argparse.Namespace) -> int:
    cases = load_cases(Path(args.cases))
    human = aggregate_human_labels(load_labels(Path(args.labels)))
    anchors, eval_cases = split_cases(cases, human)

    if args.judge_scores:
        payload = json.loads(Path(args.judge_scores).read_text(encoding="utf-8"))
        judge_scores, fingerprint = payload["scores"], payload.get("fingerprint", "cached")
    else:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            print("OPENAI_API_KEY is not set; pass --judge-scores to use cached scores.", file=sys.stderr)
            return 1
        judge = LLMJudge(openai_judge_fn(args.model, api_key), args.model, anchors)
        judge_scores, fingerprint = await score_cases(judge, eval_cases), judge.fingerprint

    eval_ids = {c["id"] for c in eval_cases}
    human_eval = {k: v for k, v in human.items() if k in eval_ids}
    judge_eval = {k: v for k, v in judge_scores.items() if k in eval_ids}
    if not human_eval or not judge_eval:
        print(f"NOT READY: no labelled eval cases (labels file: {args.labels})")
        return 2

    report = compute_agreement(human_eval, judge_eval)
    gate = evaluate_gate(report, args.threshold, args.min_cases)
    result = {
        "judge_fingerprint": fingerprint,
        "model": args.model,
        "n_anchors": len(anchors),
        "gate": {"passed": gate.passed, "ready": gate.ready, "reasons": gate.reasons},
        "report": report.to_dict(),
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
        Path(args.out).with_suffix(".scores.json").write_text(
            json.dumps({"fingerprint": fingerprint, "scores": judge_scores}, indent=2), encoding="utf-8"
        )

    print(json.dumps(result["gate"] | {"n_cases": report.n_cases, "within_one": round(report.within_one_agreement, 3)}))
    if not gate.ready:
        return 2
    return 0 if gate.passed else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cases", default=str(DATA_DIR / "golden_cases.json"))
    p.add_argument("--labels", default=str(DATA_DIR / "human_labels.jsonl"))
    p.add_argument("--judge-scores", default=None)
    p.add_argument("--model", default=os.environ.get("JUDGE_MODEL", "gpt-4o"))
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    p.add_argument("--min-cases", type=int, default=DEFAULT_MIN_CASES)
    p.add_argument("--out", default="evals/reports/judge_gate.json")
    sys.exit(asyncio.run(run(p.parse_args())))


if __name__ == "__main__":
    main()
