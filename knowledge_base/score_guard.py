"""Score documents with TypeSafe Jev at ingest time and store the result in documents.guard_score.

    python -m knowledge_base.score_guard                  # score rows with guard_score IS NULL
    python -m knowledge_base.score_guard --kinds adversarial,clean --limit 500
    python -m knowledge_base.score_guard --rescore        # also re-score rows scored by a different model

Run it after every ingest. Retrieval only reads the stored score (shared.config rag_guard_threshold), so the hosted
classifier is never on the incident path. HOSTED API: document text leaves the machine; needs TYPESAFE_API_KEY and
`pip install langchain-typesafe`. Re-run with --rescore when you change the model or the question in evals.guards.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

import asyncpg

from evals.guards import JevGuard
from shared.config import get_settings

SELECT_SQL = """
    SELECT doc_id, title, content FROM documents
    WHERE (guard_score IS NULL OR ($1::bool AND guard_model IS DISTINCT FROM $2))
      AND ($3::text[] IS NULL OR COALESCE(metadata->>'kind', '') = ANY($3))
    ORDER BY id LIMIT $4
"""
UPDATE_SQL = "UPDATE documents SET guard_score = $2, guard_model = $3, guard_scored_at = now() WHERE doc_id = $1"


async def score_documents(
    conn: asyncpg.Connection,
    guard: JevGuard,
    model: str,
    rescore: bool,
    kinds: list[str] | None,
    limit: int,
    batch: int,
) -> dict[str, Any]:
    threshold = get_settings().agent.rag_guard_threshold or 0.5
    done = failed = flagged = 0
    remaining = limit
    while remaining > 0:
        rows = await conn.fetch(SELECT_SQL, rescore, model, kinds, min(batch, remaining))
        # Rows that failed to score in an earlier batch would be re-selected forever, so stop when nothing new scores.
        rows = [r for r in rows if r["doc_id"] not in guard.failed]
        if not rows:
            break
        guard.scores.clear()
        results = await asyncio.gather(
            *[guard.score_one(r["doc_id"], f"{r['title']}\n{r['content']}") for r in rows], return_exceptions=True
        )
        for r, res in zip(rows, results, strict=True):
            if isinstance(res, BaseException):
                guard.failed.add(r["doc_id"])
                failed += 1
                continue
            await conn.execute(UPDATE_SQL, r["doc_id"], guard.scores[r["doc_id"]], model)
            done += 1
            flagged += guard.scores[r["doc_id"]] >= threshold
        remaining -= len(rows)
        print(f"scored={done} failed={failed} flagged={flagged}", flush=True)
    return {"scored": done, "failed": failed, "flagged_at_threshold": flagged, "model": model}


async def main_async(a: argparse.Namespace) -> dict[str, Any]:
    guard = JevGuard(model=a.model)
    conn = await asyncpg.connect(get_settings().data.rag_dsn, statement_cache_size=0)
    try:
        kinds = a.kinds.split(",") if a.kinds else None
        return await score_documents(conn, guard, a.model, a.rescore, kinds, a.limit, a.batch)
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=get_settings().agent.rag_guard_model, help="pin a version; jev-latest moves")
    p.add_argument("--limit", type=int, default=100_000)
    p.add_argument("--batch", type=int, default=200)
    p.add_argument("--kinds", default=None, help="comma list of metadata.kind values (real,clean,adversarial)")
    p.add_argument("--rescore", action="store_true")
    print(json.dumps(asyncio.run(main_async(p.parse_args())), indent=2))


if __name__ == "__main__":
    main()
