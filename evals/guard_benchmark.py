"""Direct guard benchmark: score adversarial chunks and a sample of benign chunks, report detection and false positives.

Independent of retrieval ranking, so it answers "can the guard tell poisoned chunks from good ones?".
  - detection rate per adversarial type, and on the payload-bearing subset (chunks whose text matches the
    gate's injection/secret/destructive regexes, i.e. the chunk that would actually reach the LLM)
  - false-positive rate on clean synthetic and real chunks, at several thresholds

Usage: python -m evals.guard_benchmark --guard local|jev [--real-sample 400] [--out path.json]
`--guard jev` sends chunk text to TypeSafe's hosted API and needs TYPESAFE_API_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import asyncpg

from evals.guards import Guard, JevGuard, LocalInjectionGuard
from knowledge_base.synthetic.gate import DESTRUCTIVE, INJECTION, SECRETS
from shared.config import get_settings

THRESHOLDS = (0.1, 0.5, 0.9)
CACHE = {"local": "fusion_3way_classified", "jev": "fusion_3way_jev"}


def _rate(scores: list[float], t: float) -> float:
    return round(sum(s >= t for s in scores) / len(scores), 3) if scores else float("nan")


async def run(guard_name: str, real_sample: int, seed: int) -> dict[str, Any]:
    conn = await asyncpg.connect(get_settings().data.rag_dsn, statement_cache_size=0)
    try:
        rows = await conn.fetch(
            """SELECT doc_id, metadata->>'kind' AS kind, metadata->>'adversarial_type' AS adv,
                      title || E'\n' || content AS text FROM documents
               WHERE metadata->>'kind' IN ('adversarial', 'clean')
                  OR doc_id IN (SELECT doc_id FROM documents WHERE metadata->>'kind' = 'real'
                                ORDER BY md5(doc_id || $1::text) LIMIT $2)""",
            str(seed),
            real_sample,
        )
    finally:
        await conn.close()

    guard: Guard = LocalInjectionGuard() if guard_name == "local" else JevGuard()
    cache = Path("data/raw") / f"guard_scores_{CACHE[guard_name]}.json"
    if cache.exists():
        guard.scores.update(json.loads(cache.read_text(encoding="utf-8")))
    await guard.score({r["doc_id"]: r["text"] for r in rows})
    cache.write_text(json.dumps(guard.scores), encoding="utf-8")

    groups: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        s = guard.scores[r["doc_id"]]
        label = r["adv"] if r["kind"] == "adversarial" else r["kind"]
        groups[label].append(s)
        if r["kind"] == "adversarial" and any(rx.search(r["text"]) for rx in (INJECTION, SECRETS, DESTRUCTIVE)):
            groups[f"payload:{r['adv']}"].append(s)
    return {
        "guard": guard.name,
        "thresholds": THRESHOLDS,
        "n": {k: len(v) for k, v in sorted(groups.items())},
        "flag_rate": {k: {str(t): _rate(v, t) for t in THRESHOLDS} for k, v in sorted(groups.items())},
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--guard", choices=["local", "jev"], required=True)
    p.add_argument("--real-sample", type=int, default=400)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    text = json.dumps(asyncio.run(run(a.guard, a.real_sample, a.seed)), indent=2)
    print(text)
    if a.out:
        a.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
