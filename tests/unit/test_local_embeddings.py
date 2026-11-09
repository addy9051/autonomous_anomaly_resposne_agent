"""Local FastEmbed embeddings: dimension guard, fail-closed loading, caching, async behaviour and provider dispatch."""

from __future__ import annotations

import asyncio
import threading
from typing import TYPE_CHECKING, Any

import pytest

from shared import local_embeddings
from shared.config import get_settings
from shared.llm import get_embeddings
from shared.local_embeddings import LocalEmbeddings, LocalEmbeddingsUnavailableError

if TYPE_CHECKING:
    from pathlib import Path


class _Vec(list):
    """Stands in for the numpy arrays FastEmbed yields; only iteration is used."""


class _FakeModel:
    def __init__(self, dim: int = 768) -> None:
        self.dim = dim
        self.threads: list[int] = []

    def embed(self, texts: list[str]) -> Any:  # noqa: ANN401
        self.threads.append(threading.get_ident())
        return (_Vec([float(len(t))] * self.dim) for t in texts)


def _embeddings(model: _FakeModel, expected: int = 768) -> LocalEmbeddings:
    return LocalEmbeddings(model_name="custom/model", expected_dimensions=expected, model_loader=lambda *_: model)


def test_embeds_documents_and_queries_with_the_same_vectors() -> None:
    emb = _embeddings(_FakeModel())
    docs = emb.embed_documents(["abc", "de"])
    assert [len(v) for v in docs] == [768, 768]
    assert docs[0][0] == 3.0
    assert emb.embed_query("abc") == docs[0]
    assert emb.embed_documents([]) == []


def test_wrong_output_size_is_refused_on_first_use() -> None:
    emb = _embeddings(_FakeModel(dim=384), expected=768)
    with pytest.raises(LocalEmbeddingsUnavailableError, match="384-dimension"):
        emb.embed_query("x")


def test_known_model_with_the_wrong_size_is_refused_before_any_load() -> None:
    with pytest.raises(LocalEmbeddingsUnavailableError, match="pgvector column size"):
        LocalEmbeddings(model_name="BAAI/bge-small-en-v1.5", expected_dimensions=768)
    assert isinstance(LocalEmbeddings(model_name="BAAI/bge-base-en-v1.5", expected_dimensions=768), LocalEmbeddings)


def test_loading_fails_closed_when_the_model_is_not_provisioned(tmp_path: Path) -> None:
    """The air-gap guarantee: no download is attempted and nothing is written when weights are missing."""
    local_embeddings._load_model.cache_clear()
    cache = tmp_path / "empty-cache"
    cache.mkdir()
    emb = LocalEmbeddings(model_name="BAAI/bge-base-en-v1.5", cache_dir=str(cache), expected_dimensions=768)
    with pytest.raises(LocalEmbeddingsUnavailableError, match="Provision it once"):
        emb.embed_query("x")
    assert list(cache.rglob("*")) == []


def test_model_is_loaded_once_per_process(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    constructed: list[tuple[str, str]] = []

    class FakeTextEmbedding(_FakeModel):
        def __init__(self, model_name: str, cache_dir: str, local_files_only: bool) -> None:
            assert local_files_only is True
            constructed.append((model_name, cache_dir))
            super().__init__()

        @staticmethod
        def list_supported_models() -> list[dict[str, Any]]:
            return [{"model": "BAAI/bge-base-en-v1.5", "dim": 768}]

    import fastembed

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTextEmbedding)
    local_embeddings._load_model.cache_clear()
    for _ in range(3):
        LocalEmbeddings(cache_dir=str(tmp_path)).embed_query("x")
    assert len(constructed) == 1
    local_embeddings._load_model.cache_clear()


async def test_async_embedding_runs_off_the_event_loop() -> None:
    model = _FakeModel()
    emb = _embeddings(model)
    loop_thread = threading.get_ident()
    vectors = await emb.aembed_documents(["a", "bb"])
    query = await emb.aembed_query("a")
    assert vectors[0] == query
    assert model.threads and all(t != loop_thread for t in model.threads)
    await asyncio.gather(*(emb.aembed_query(str(i)) for i in range(5)))


def test_provider_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = get_settings().llm
    monkeypatch.setattr(llm, "embedding_provider", "fastembed")
    monkeypatch.setattr(llm, "embedding_local_model", "BAAI/bge-base-en-v1.5")
    monkeypatch.setattr(llm, "embedding_dimensions", 768)
    assert isinstance(get_embeddings(), LocalEmbeddings)

    monkeypatch.setattr(llm, "embedding_provider", "openai_compatible")
    assert type(get_embeddings()).__name__ == "OpenAIEmbeddings"

    monkeypatch.setattr(llm, "embedding_provider", "something-else")
    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        get_embeddings()


def test_provisioning_refuses_to_download_while_egress_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings().app, "egress_enforce", True)
    with pytest.raises(LocalEmbeddingsUnavailableError, match="egress enforcement is on"):
        local_embeddings.provision("BAAI/bge-base-en-v1.5", "models/never-created")
    assert local_embeddings.main(["provision"]) == 2
