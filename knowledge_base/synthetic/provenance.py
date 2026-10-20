"""Provenance and licensing for real (downloaded) knowledge-base sources.

Each top-level folder under ``data/kb/real`` maps to one ``SourceSpec``. ``allow_generation`` gates whether a
source's text may be used as grounding for LLM-generated runbooks; ``allow_ingest`` gates retrieval ingestion.
Defaults are conservative: anything with a non-commercial, no-derivatives, share-alike or unverified license
is excluded from generation. Verify licenses yourself before redistributing anything.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from knowledge_base.retrieval.bm25 import BM25Corpus

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class SourceSpec:
    folder: str
    source: str
    license: str
    url: str
    allow_ingest: bool = True
    allow_generation: bool = True
    notes: str = ""


SOURCES: dict[str, SourceSpec] = {
    s.folder: s
    for s in (
        SourceSpec("openshift", "openshift/runbooks", "Apache-2.0", "https://github.com/openshift/runbooks"),
        SourceSpec(
            "prometheus",
            "prometheus-operator/runbooks",
            "Apache-2.0",
            "https://github.com/prometheus-operator/runbooks",
        ),
        SourceSpec("gitlab", "gitlab-com/runbooks", "MIT", "https://gitlab.com/gitlab-com/runbooks"),
        SourceSpec(
            "k8s-docs",
            "kubernetes docs + kubernetes-mixin",
            "CC-BY-4.0 (docs); Apache-2.0 (mixin)",
            "https://github.com/kubernetes/website",
        ),
        SourceSpec(
            "postgres-docs",
            "postgresql.org docs",
            "PostgreSQL License",
            "https://www.postgresql.org/docs/current/",
        ),
        SourceSpec(
            "redis-docs",
            "redis.io docs",
            "CC-BY-SA-4.0",
            "https://redis.io/docs/",
            allow_generation=False,
            notes="Share-alike: kept out of generation to avoid license propagation.",
        ),
        SourceSpec(
            "nginx-envoy-docs",
            "nginx / ingress-nginx / envoy docs",
            "mixed (Apache-2.0 Envoy and ingress-nginx; nginx.org unverified)",
            "https://www.envoyproxy.io/docs/",
            allow_generation=False,
            notes="Mixed licenses, one unverified: excluded from generation.",
        ),
        SourceSpec(
            "sre-book",
            "Google SRE book",
            "CC-BY-NC-ND-4.0",
            "https://sre.google/sre-book/",
            allow_generation=False,
            notes="No-derivatives and non-commercial: must never ground generated text.",
        ),
        SourceSpec(
            "postmortems",
            "public engineering postmortems",
            "proprietary (company blogs)",
            "see per-file source header",
            allow_generation=False,
            notes="Fetched for local retrieval testing only; do not redistribute or paraphrase into generated data.",
        ),
    )
}

# gitlab/<service> -> collection
_GITLAB_COLLECTION = {
    "database": {
        "patroni",
        "patroni-ci",
        "pgbouncer",
        "pgbouncer-ci",
        "redis",
        "redis-cluster-cache",
        "redis-tracechunks",
        "elastic",
        "clickhouse",
        "cloud-sql",
    },
    "network": {"cloudflare", "consul", "nginx", "istio", "external-dns", "certificates", "blackbox"},
    "general": {"incidents", "disaster-recovery", "alerts"},
}
_KEYWORD_COLLECTION = (
    ("security", ("cert", "tls", "auth", "oauth", "waf", "secret", "rbac", "vulnerab")),
    ("database", ("etcd", "postgres", "redis", "mysql", "kafka", "database", "pg_", "wal", "replica", "vacuum")),
    ("network", ("dns", "ingress", "network", "haproxy", "nginx", "envoy", "cni", "proxy", "latency", "bgp")),
    ("application", ("pod", "deployment", "kubelet", "node", "container", "memory", "cpu", "daemonset", "jvm")),
)


def classify_collection(spec: SourceSpec, rel: Path, text: str) -> str:
    """Map a real file to one of database/network/security/application/general."""
    if spec.folder == "gitlab" and rel.parts:
        for coll, names in _GITLAB_COLLECTION.items():
            if rel.parts[0] in names:
                return coll
        return "application"
    if spec.folder in {"postgres-docs", "redis-docs"}:
        return "database"
    if spec.folder == "nginx-envoy-docs":
        return "network"
    if spec.folder in {"sre-book", "postmortems"}:
        return "general"
    probe = (str(rel) + " " + text[:600]).lower()
    for coll, words in _KEYWORD_COLLECTION:
        if any(w in probe for w in words):
            return coll
    return "general"


def _title(text: str, stem: str) -> str:
    m = re.search(r"^#{1,3}\s+(.+?)\s*(?:\[#.*)?$", text, re.MULTILINE)
    return (m.group(1) if m else stem.replace("-", " ").replace("_", " ")).strip()[:120]


def build_manifest(root: Path) -> list[dict[str, object]]:
    """Walk ``root/<source folder>/**/*.md`` and describe every file."""
    rows: list[dict[str, object]] = []
    for spec in SOURCES.values():
        base = root / spec.folder
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            rel = f.relative_to(base)
            m = re.match(r"<!-- source: (\S+) -->", text)
            rows.append(
                {
                    "path": f.relative_to(root).as_posix(),
                    "title": _title(text, f.stem),
                    "source": spec.source,
                    "source_folder": spec.folder,
                    "license": spec.license,
                    "origin_url": m.group(1) if m else spec.url,
                    "collection": classify_collection(spec, rel, text),
                    "allow_ingest": spec.allow_ingest,
                    "allow_generation": spec.allow_generation,
                    "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
                    "chars": len(text),
                    "kind": "real",
                    "trust_tier": "trusted-external",
                }
            )
    return rows


def write_manifest(root: Path) -> Path:
    out = root / "MANIFEST.jsonl"
    rows = build_manifest(root)
    out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (root / "SOURCES.json").write_text(json.dumps([asdict(s) for s in SOURCES.values()], indent=2), encoding="utf-8")
    return out


def load_manifest(root: Path) -> list[dict[str, object]]:
    path = root / "MANIFEST.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class GroundingIndex:
    """BM25 over generation-allowed real runbooks; returns a short excerpt for a seed query."""

    def __init__(self, root: Path, excerpt_chars: int = 1800) -> None:
        self.excerpt_chars = excerpt_chars
        self.meta: dict[str, dict[str, object]] = {}
        docs: dict[str, str] = {}
        for row in load_manifest(root):
            if not row["allow_generation"]:
                continue
            text = (root / str(row["path"])).read_text(encoding="utf-8", errors="replace")
            text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
            if len(text) < 600:
                continue
            docs[str(row["path"])] = text
            self.meta[str(row["path"])] = row
        self.docs = docs
        self.corpus = BM25Corpus.build(docs) if docs else None

    def excerpt(self, query: str, pick: int = 0) -> tuple[str, dict[str, object]] | None:
        if not self.corpus:
            return None
        ranked = self.corpus.rank(query, list(self.docs), limit=3)
        if not ranked:
            return None
        path = ranked[pick % len(ranked)][0]
        return self.docs[path][: self.excerpt_chars], self.meta[path]
