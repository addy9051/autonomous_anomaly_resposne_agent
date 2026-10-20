"""Manifest-driven ingestion of the real corpus.

    python -m knowledge_base.synthetic.ingest_real --root data/kb/real            # writes manifest, ingests
    python -m knowledge_base.synthetic.ingest_real --root data/kb/real --dry-run  # manifest + counts only

Only rows with allow_ingest=true are loaded. License and origin travel with each chunk in ``metadata``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from knowledge_base.ingestion.pipeline import RunbookIngestionPipeline
from knowledge_base.synthetic.provenance import load_manifest, write_manifest


async def ingest(root: Path, concurrency: int, limit: int | None) -> dict[str, object]:
    rows = [r for r in load_manifest(root) if r["allow_ingest"]][:limit]
    pipeline = RunbookIngestionPipeline()
    sem = asyncio.Semaphore(concurrency)
    chunks = 0
    failed: list[str] = []

    async def one(row: dict[str, object]) -> None:
        nonlocal chunks
        path = str(row["path"])
        text = (root / path).read_text(encoding="utf-8", errors="replace")
        async with sem:
            try:
                ids = await pipeline.ingest_document(
                    title=f"{row['title']} ({path.removesuffix('.md')})",
                    content=text,
                    source=str(row["source"]),
                    collection=str(row["collection"]),
                    metadata={
                        "license": row["license"],
                        "origin_url": row["origin_url"],
                        "path": path,
                        "sha256": row["sha256"],
                        "kind": "real",
                        "trust_tier": row["trust_tier"],
                        "allow_generation": row["allow_generation"],
                    },
                )
                chunks += len(ids)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{path}: {type(exc).__name__}: {str(exc)[:80]}")

    await asyncio.gather(*[one(r) for r in rows])
    return {"files": len(rows), "chunks": chunks, "failed": failed[:10], "n_failed": len(failed)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path("data/kb/real"))
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    write_manifest(a.root)
    rows = load_manifest(a.root)
    summary = {
        "files": len(rows),
        "by_source": dict(Counter(str(r["source_folder"]) for r in rows)),
        "by_collection": dict(Counter(str(r["collection"]) for r in rows)),
        "generation_allowed": sum(bool(r["allow_generation"]) for r in rows),
        "generation_blocked": sum(not r["allow_generation"] for r in rows),
    }
    print(json.dumps(summary, indent=2))
    if not a.dry_run:
        print(json.dumps(asyncio.run(ingest(a.root, a.concurrency, a.limit)), indent=2))


if __name__ == "__main__":
    main()
