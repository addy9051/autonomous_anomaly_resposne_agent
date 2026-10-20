"""Ingest the output of ``knowledge_base.synthetic.build`` (clean runbooks and, optionally, adversarial docs).

    python -m knowledge_base.synthetic.ingest_synth --root data/kb/synth                      # clean only
    python -m knowledge_base.synthetic.ingest_synth --root data/kb/synth --include-adversarial

Adversarial docs go into their target's collection (so a poisoning eval can retrieve them) with
``trust_tier=untrusted`` and an ``[ADV:<type>]`` title prefix. Remove them with:
    DELETE FROM documents WHERE metadata->>'kind' = 'adversarial';
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from knowledge_base.ingestion.pipeline import RunbookIngestionPipeline


def _file_for(root: Path, row: dict[str, object]) -> Path | None:
    rid = str(row["id"])
    if row["kind"] == "adversarial":
        folder, suffix = root / "adversarial", f"-{rid[:8]}.md"
    else:
        folder, suffix = root / str(row["collection"]), f"-{rid[:6]}.md"
    return next(iter(folder.glob(f"*{suffix}")), None) if folder.is_dir() else None


async def ingest(root: Path, include_adversarial: bool, concurrency: int, limit: int | None) -> dict[str, object]:
    rows = [json.loads(line) for line in (root / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
    rows = [r for r in rows if include_adversarial or r["kind"] != "adversarial"][:limit]
    pipeline = RunbookIngestionPipeline()
    sem = asyncio.Semaphore(concurrency)
    counts = {"clean": 0, "adversarial": 0, "chunks": 0}
    failed: list[str] = []

    async def one(row: dict[str, object]) -> None:
        path = _file_for(root, row)
        if path is None:
            failed.append(f"{row['id']}: file not found")
            return
        text = path.read_text(encoding="utf-8")
        adv = row["kind"] == "adversarial"
        title = path.stem.replace("-", " ").title()
        if adv:
            title = f"[ADV:{row['adversarial_type']}] {title}"
        async with sem:
            try:
                ids = await pipeline.ingest_document(
                    title=title,
                    content=text,
                    source=str(row["source"]),
                    collection=str(row["collection"]),
                    service_tags=list(row.get("service_tags") or []),  # type: ignore[call-overload]
                    metadata={
                        "kind": row["kind"],
                        "trust_tier": row["trust_tier"],
                        "adversarial_type": row.get("adversarial_type"),
                        "target_id": row.get("target_id"),
                        "grounded_on": row.get("grounded_on"),
                        "grounded_license": row.get("grounded_license"),
                        "synthetic_id": row["id"],
                    },
                )
                counts["adversarial" if adv else "clean"] += 1
                counts["chunks"] += len(ids)
            except Exception as exc:  # noqa: BLE001
                failed.append(f"{path.name}: {type(exc).__name__}: {str(exc)[:80]}")

    await asyncio.gather(*[one(r) for r in rows])
    return {**counts, "failed": failed[:10], "n_failed": len(failed)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path("data/kb/synth"))
    p.add_argument("--include-adversarial", action="store_true")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--limit", type=int, default=None)
    a = p.parse_args()
    print(json.dumps(asyncio.run(ingest(a.root, a.include_adversarial, a.concurrency, a.limit)), indent=2))


if __name__ == "__main__":
    main()
