"""Tests for provenance gating and grounding."""

from __future__ import annotations

from typing import TYPE_CHECKING

from knowledge_base.synthetic.gate import copied_fraction
from knowledge_base.synthetic.provenance import SOURCES, GroundingIndex, build_manifest, write_manifest

if TYPE_CHECKING:
    from pathlib import Path

BODY = "Symptoms: postgres replication lag grows. Check pg_stat_replication for replay delay. " * 12


def _tree(root: Path) -> None:
    for folder in ("openshift", "sre-book", "redis-docs"):
        (root / folder).mkdir(parents=True)
        (root / folder / "a.md").write_text(f"# {folder} postgres replication lag\n\n{BODY}", encoding="utf-8")


def test_blocked_sources_are_flagged() -> None:
    assert SOURCES["sre-book"].allow_generation is False
    assert SOURCES["postmortems"].allow_generation is False
    assert SOURCES["redis-docs"].allow_generation is False
    assert SOURCES["openshift"].allow_generation is True


def test_manifest_carries_source_and_license(tmp_path: Path) -> None:
    _tree(tmp_path)
    rows = {r["source_folder"]: r for r in build_manifest(tmp_path)}
    assert rows["sre-book"]["license"] == "CC-BY-NC-ND-4.0"
    assert rows["sre-book"]["allow_generation"] is False
    assert rows["sre-book"]["allow_ingest"] is True
    assert rows["openshift"]["source"] == "openshift/runbooks"


def test_grounding_never_returns_blocked_sources(tmp_path: Path) -> None:
    _tree(tmp_path)
    write_manifest(tmp_path)
    idx = GroundingIndex(tmp_path)
    assert set(idx.docs) == {"openshift/a.md"}
    got = idx.excerpt("postgres replication lag")
    assert got is not None
    assert got[1]["source_folder"] == "openshift"


def test_copied_fraction() -> None:
    src = "check the replication slot lag on the primary and then restart the replica carefully"
    assert copied_fraction(src, src) == 1.0
    assert copied_fraction("completely different words about caching layers and eviction policy", src) == 0.0
