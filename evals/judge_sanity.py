"""
Judge sanity check (no human labels needed): run the live judge over the golden cases and verify that
it follows the rubric. Cases the deterministic layers flag (violations/findings) are known-flawed, so a
working judge must (1) return valid rubric JSON and (2) score clean cases clearly above flawed ones.

  python -m evals.judge_sanity [--limit N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path

from evals.judge import LLMJudge, local_judge_fn
from evals.provider_check import JudgeIndependenceError, choose_independent_judge
from evals.rubric import ACCEPTABLE_OVERALL
from shared.config import get_settings

CASES = Path(__file__).parent / "data" / "golden_cases.json"
MIN_SEPARATION = 1.0  # clean mean must exceed flawed mean by this many rubric points
CONCURRENCY = 4


async def run(limit: int | None, model: str, out: Path) -> int:
    try:
        model = choose_independent_judge(model, get_settings().llm)
    except JudgeIndependenceError as exc:
        print(f"BLOCKED: {exc}")
        return 3

    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"][:limit]
    judge = LLMJudge(local_judge_fn(model), model)
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(case: dict) -> dict:
        async with sem:
            expect = case.get("expect", {})
            flawed = bool(expect.get("violations") or expect.get("findings"))
            try:
                v = await judge.score(case)
                return {
                    "id": case["id"],
                    "flawed": flawed,
                    "overall": v.overall,
                    "scores": v.scores,
                    "why": v.justification,
                }
            except Exception as exc:  # noqa: BLE001 - a bad judge response is a result, not a crash
                return {"id": case["id"], "flawed": flawed, "error": str(exc)[:200]}

    rows = await asyncio.gather(*(one(c) for c in cases))
    ok = [r for r in rows if "overall" in r]
    clean = [r["overall"] for r in ok if not r["flawed"]]
    flawed = [r["overall"] for r in ok if r["flawed"]]
    mean = lambda xs: round(statistics.mean(xs), 2) if xs else None  # noqa: E731
    clean_ok = sum(o >= ACCEPTABLE_OVERALL for o in clean)
    flawed_ok = sum(o >= ACCEPTABLE_OVERALL for o in flawed)

    summary = {
        "model": model,
        "cases": len(rows),
        "valid_responses": len(ok),
        "clean_mean": mean(clean),
        "flawed_mean": mean(flawed),
        "clean_rated_acceptable": f"{clean_ok}/{len(clean)}",
        "flawed_rated_acceptable": f"{flawed_ok}/{len(flawed)}",
    }
    separation = (mean(clean) - mean(flawed)) if clean and flawed else None
    summary["separation"] = round(separation, 2) if separation is not None else None
    passed = len(ok) == len(rows) and separation is not None and separation >= MIN_SEPARATION
    summary["passed"] = passed

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    for r in rows:
        tag = "FLAWED" if r["flawed"] else "clean "
        print(f"{tag} {r['id']:<40} {r.get('overall', r.get('error'))}")
    print(json.dumps(summary, indent=2))
    return 0 if passed else 1


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--model", default=get_settings().llm.judge_model)
    p.add_argument("--out", default="evals/reports/judge_sanity.json")
    args = p.parse_args()
    sys.exit(asyncio.run(run(args.limit, args.model, Path(args.out))))


if __name__ == "__main__":
    main()
