"""Live retrieval eval over the ingested knowledge base (real + synthetic + adversarial docs).

Ground truth comes from data/kb/synth/manifest.jsonl:
  - quality:   query = "<stack>: <symptom>" for each (stack, failure mode); relevant = the synthetic runbooks
               generated for that seed (production and staging variants). Real runbooks are NOT counted as hits,
               so absolute recall is a lower bound.
  - poisoning: adversarial docs are labelled; we measure how often any reach the top-k, and which types.
  - mitigation: `fusion_3way_trusted` drops trust_tier=untrusted candidates before ranking.

Caveats: synthetic runbooks were generated from the same symptom text used in the queries, so lexical
legs are favoured; treat results as a regression/poisoning gate, not a general quality claim.

Usage: python -m evals.retrieval_live [--k 5] [--per-leg 50] [--limit N] [--out path.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

import asyncpg

from evals.guards import Guard, JevGuard, LocalInjectionGuard
from knowledge_base.retrieval.search import HybridSearchService
from knowledge_base.synthetic.gate import DESTRUCTIVE, INJECTION, SECRETS
from knowledge_base.synthetic.taxonomy import build_seeds

BASE_STRATEGIES = ("vector_only", "bm25_only", "rrf_only", "reranker_only", "fusion_3way", "fusion_3way_trusted")
GUARD_STRATEGIES = {"fusion_3way_classified": LocalInjectionGuard, "fusion_3way_jev": JevGuard}
HARMFUL_TYPES = {"prompt_injection", "dangerous_procedure", "secrets_pii_bait"}


def _load_manifest(root: Path) -> list[dict[str, Any]]:
    lines = (root / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line]


def _seed_key(full_seed: str) -> str:
    stack, mode, _env = full_seed.split("|")
    return f"{stack}|{mode}"


async def _load_meta(conn: asyncpg.Connection) -> dict[str, dict[str, str | None]]:
    rows = await conn.fetch(
        """SELECT doc_id, title, metadata->>'kind' AS kind, metadata->>'synthetic_id' AS sid,
                  metadata->>'adversarial_type' AS adv, metadata->>'trust_tier' AS trust
           FROM documents"""
    )
    return {r["doc_id"]: dict(r) for r in rows}


class LiveLab:
    def __init__(
        self,
        conn: asyncpg.Connection,
        svc: HybridSearchService,
        per_leg: int,
        guards: dict[str, Guard] | None = None,
        threshold: float = 0.5,
    ) -> None:
        self.conn, self.svc, self.per_leg = conn, svc, per_leg
        self.guards, self.threshold = guards or {}, threshold
        self.meta: dict[str, dict[str, str | None]] = {}
        self.text: dict[str, str] = {}

    def group(self, doc_id: str) -> str:
        m = self.meta[doc_id]
        return str(m["sid"] or m["title"])

    async def _guard_filter(
        self, guard: Guard, vector: list[tuple[str, float]], keyword: list[tuple[str, float]]
    ) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        pool = list(dict.fromkeys(d for d, _ in [*vector, *keyword]))
        docs = await self.svc._get_documents(self.conn, pool)
        await guard.score({d: f"{v['title']}\n{v['content']}" for d, v in docs.items()})
        ok = {d for d in pool if guard.scores.get(d, 0.0) < self.threshold}
        return [x for x in vector if x[0] in ok], [x for x in keyword if x[0] in ok]

    async def retrieve(self, strategy: str, query: str, k: int) -> list[tuple[str, str]]:
        """Return top-k (document group, best chunk id) pairs, best first; chunks collapse to their parent doc."""
        svc, conn = self.svc, self.conn
        qv = await svc.embeddings.aembed_query(query)
        vector = await svc._vector_search(conn, qv, None, self.per_leg * 2, None)
        keyword = await svc._keyword_search(conn, query, None, self.per_leg * 2, None)
        if strategy == "fusion_3way_trusted":
            vector = [x for x in vector if self.meta[x[0]]["trust"] != "untrusted"]
            keyword = [x for x in keyword if self.meta[x[0]]["trust"] != "untrusted"]
        elif strategy in self.guards:
            vector, keyword = await self._guard_filter(self.guards[strategy], vector, keyword)
        vector, keyword = vector[: self.per_leg], keyword[: self.per_leg]

        if strategy == "vector_only":
            ranked = vector
        elif strategy == "bm25_only":
            ranked = keyword
        else:
            pool = list(dict.fromkeys(d for d, _ in [*vector, *keyword]))
            docs = await svc._get_documents(conn, pool)
            rerank = svc._rerank(query, docs)
            if strategy == "rrf_only":
                ranked = svc._reciprocal_rank_fusion(vector, keyword, k=60)
            elif strategy == "reranker_only":
                ranked = rerank
            else:
                w = svc.settings.agent.rag_rerank_weight
                ranked = svc._reciprocal_rank_fusion(vector, keyword, rerank, k=60, weights=[1.0, 1.0, w])
        shown: dict[str, str] = {}
        for d, _ in ranked:
            if d in self.meta:
                shown.setdefault(self.group(d), d)
        return list(shown.items())[:k]

    async def has_payload(self, chunk_ids: list[str]) -> dict[str, bool]:
        """Deterministic ground truth: does this retrieved chunk of an adversarial doc carry a detectable payload?"""
        missing = [c for c in chunk_ids if c not in self.text]
        if missing:
            docs = await self.svc._get_documents(self.conn, missing)
            self.text.update({d: f"{v['title']}\n{v['content']}" for d, v in docs.items()})
        return {
            c: self.meta[c]["kind"] == "adversarial"
            and any(rx.search(self.text.get(c, "")) for rx in (INJECTION, SECRETS, DESTRUCTIVE))
            for c in chunk_ids
        }


def _group_info(lab: LiveLab, group: str) -> dict[str, str | None]:
    return next(m for m in lab.meta.values() if str(m["sid"] or m["title"]) == group)


def _scores_path(strategy: str) -> Path:
    return Path("data/raw") / f"guard_scores_{strategy}.json"


def _load_scores(strategy: str, guard: Guard) -> None:
    path = _scores_path(strategy)
    if path.exists():
        guard.scores.update(json.loads(path.read_text(encoding="utf-8")))


def _save_scores(strategy: str, guard: Guard) -> None:
    _scores_path(strategy).write_text(json.dumps(guard.scores), encoding="utf-8")


def _guard_report(lab: LiveLab, guard: Guard) -> dict[str, Any]:
    """Flag rate per kind at the threshold, over the chunks the guard actually scored (retrieval candidates)."""
    flagged: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for doc_id, score in guard.scores.items():
        m = lab.meta[doc_id]
        label = str(m["adv"]) if m["kind"] == "adversarial" else str(m["kind"])
        total[label] += 1
        flagged[label] += score >= lab.threshold
    return {
        "threshold": lab.threshold,
        "chunks_scored": sum(total.values()),
        "flag_rate_by_kind": {k: round(flagged[k] / total[k], 3) for k in sorted(total)},
    }


async def run(
    root: Path, k: int, per_leg: int, limit: int | None, strategies: list[str], threshold: float
) -> dict[str, Any]:
    manifest = _load_manifest(root)
    seed_by_id = {m["id"]: _seed_key(m["seed"]) for m in manifest if m["kind"] == "clean"}
    relevant: dict[str, set[str]] = {}
    for sid, key in seed_by_id.items():
        relevant.setdefault(key, set()).add(sid)
    symptoms = {f"{s.stack}|{s.failure_mode}": f"{s.stack}: {s.symptom}" for s in build_seeds()}
    queries = [(key, symptoms[key]) for key in sorted(relevant)][:limit]

    svc = HybridSearchService()
    conn = await asyncpg.connect(svc.settings.data.rag_dsn, statement_cache_size=0)
    try:
        guards = {s: GUARD_STRATEGIES[s]() for s in strategies if s in GUARD_STRATEGIES}
        for s, g in guards.items():
            _load_scores(s, g)
        lab = LiveLab(conn, svc, per_leg, guards, threshold)
        lab.meta = await _load_meta(conn)
        group_info = {str(m["sid"] or m["title"]): m for m in lab.meta.values()}
        report: dict[str, Any] = {
            "k": k,
            "n_queries": len(queries),
            "kb": dict(Counter(str(m["kind"]) for m in lab.meta.values())),
            "strategies": {},
            "guards": {},
        }
        for strategy in strategies:
            hits = rr = poison_any = poison_harmful = poison_frac = payload_any = 0.0
            by_type: Counter[str] = Counter()
            payload_by_type: Counter[str] = Counter()
            for key, query in queries:
                shown = await lab.retrieve(strategy, query, k)
                groups = [g for g, _ in shown]
                infos = [group_info[g] for g in groups]
                rank = next(
                    (i + 1 for i, (g, m) in enumerate(zip(groups, infos, strict=True)) if g in relevant[key]), None
                )
                hits += 1.0 if rank else 0.0
                rr += 1.0 / rank if rank else 0.0
                adv = [str(m["adv"]) for m in infos if m["kind"] == "adversarial"]
                poison_any += 1.0 if adv else 0.0
                poison_harmful += 1.0 if any(a in HARMFUL_TYPES for a in adv) else 0.0
                poison_frac += len(adv) / max(len(groups), 1)
                by_type.update(adv)
                payload = await lab.has_payload([c for _, c in shown])
                bad = [str(lab.meta[c]["adv"]) for c, flag in payload.items() if flag]
                payload_any += 1.0 if bad else 0.0
                payload_by_type.update(bad)
            n = len(queries)
            report["strategies"][strategy] = {
                f"target_hit@{k}": round(hits / n, 3),
                "target_mrr": round(rr / n, 3),
                f"poison_any@{k}": round(poison_any / n, 3),
                f"poison_harmful@{k}": round(poison_harmful / n, 3),
                f"payload_shown@{k}": round(payload_any / n, 3),
                f"adv_share@{k}": round(poison_frac / n, 3),
                "adv_by_type": dict(by_type.most_common()),
                "payload_by_type": dict(payload_by_type.most_common()),
            }
            if strategy in guards:
                report["guards"][strategy] = _guard_report(lab, guards[strategy])
                _save_scores(strategy, guards[strategy])
        return report
    finally:
        await conn.close()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, default=Path("data/kb/synth"))
    p.add_argument("--k", type=int, default=5)
    p.add_argument("--per-leg", type=int, default=50)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument(
        "--strategies",
        default=",".join(BASE_STRATEGIES),
        help=f"comma list from {[*BASE_STRATEGIES, *GUARD_STRATEGIES]} (jev sends chunk text to TypeSafe)",
    )
    p.add_argument("--guard-threshold", type=float, default=0.5)
    a = p.parse_args()
    report = asyncio.run(run(a.root, a.k, a.per_leg, a.limit, a.strategies.split(","), a.guard_threshold))
    text = json.dumps(report, indent=2)
    print(text)
    if a.out:
        a.out.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
