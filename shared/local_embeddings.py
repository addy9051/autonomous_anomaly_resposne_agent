"""Local CPU embeddings (FastEmbed / ONNX) as an alternative to the in-VPC inference server.

Why: it removes the embedding endpoint from the runtime path, so retrieval keeps working with no network at all.
The default model, BAAI/bge-base-en-v1.5, produces 768-dimension vectors. That is the size of the existing
``documents.embedding`` pgvector column and the same model the Modal/vLLM embedding server serves, so switching
providers needs no schema change and no re-embedding. A model with a different size (for example bge-small, 384)
is refused unless ``EMBEDDING_DIMENSIONS`` and the column are changed together.

Air-gap behaviour: FastEmbed downloads weights from the internet when they are missing, and that download does
not pass through ``shared.egress``. So loading always runs with ``local_files_only=True`` and fails closed when
the model has not been provisioned. Provisioning is an explicit step done once on a connected machine
(``python -m shared.local_embeddings provision``); the cache directory is then copied into the sandbox. The
provision command refuses to run while egress enforcement is on.

Queries and documents are embedded the same way, with no query instruction prefix, matching what the
OpenAI-compatible server does for the same model.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langchain_core.embeddings import Embeddings

from shared.config import get_settings
from shared.utils import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("local_embeddings")

DEFAULT_MODEL = "BAAI/bge-base-en-v1.5"
DEFAULT_CACHE_DIR = "models/fastembed"
PROVISION_HINT = (
    "Provision it once on a machine with internet access and EGRESS_ENFORCE=false: "
    "`python -m shared.local_embeddings provision`, then copy the cache directory into this environment."
)


class LocalEmbeddingsUnavailableError(RuntimeError):
    """The local embedding model cannot be used (not provisioned, or the wrong size)."""


def _known_dimension(model_name: str) -> int | None:
    """Output size from FastEmbed's static model catalogue (no network, no model load)."""
    from fastembed import TextEmbedding

    for entry in TextEmbedding.list_supported_models():
        if entry.get("model") == model_name:
            return int(entry["dim"])
    return None


@lru_cache(maxsize=2)
def _load_model(model_name: str, cache_dir: str) -> Any:  # noqa: ANN401 - fastembed type is optional at import time
    """Load once per process. Retrieval services are built per request, and loading ONNX weights is slow."""
    from fastembed import TextEmbedding

    try:
        return TextEmbedding(model_name=model_name, cache_dir=cache_dir, local_files_only=True)
    except Exception as exc:
        raise LocalEmbeddingsUnavailableError(
            f"Embedding model {model_name!r} is not available in {cache_dir!r} ({type(exc).__name__}). {PROVISION_HINT}"
        ) from exc


class LocalEmbeddings(Embeddings):
    """langchain Embeddings backed by an on-disk FastEmbed model."""

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        cache_dir: str = DEFAULT_CACHE_DIR,
        expected_dimensions: int = 768,
        model_loader: Callable[[str, str], Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.cache_dir = cache_dir
        self.expected_dimensions = expected_dimensions
        self._model_loader = model_loader or _load_model
        self._checked = False
        self._check_dimensions_from_catalogue()

    def _check_dimensions_from_catalogue(self) -> None:
        dim = _known_dimension(self.model_name) if self._model_loader is _load_model else None
        if dim is not None and dim != self.expected_dimensions:
            raise LocalEmbeddingsUnavailableError(
                f"{self.model_name} produces {dim}-dimension vectors but EMBEDDING_DIMENSIONS is "
                f"{self.expected_dimensions} (the pgvector column size). Use a model with matching size, or change "
                "the column and the setting together and re-embed."
            )

    def _model(self) -> Any:  # noqa: ANN401
        return self._model_loader(self.model_name, self.cache_dir)

    def _verify(self, vectors: list[list[float]]) -> list[list[float]]:
        """Check the real output size once, which also covers models missing from the static catalogue."""
        if not self._checked and vectors:
            if len(vectors[0]) != self.expected_dimensions:
                raise LocalEmbeddingsUnavailableError(
                    f"{self.model_name} returned {len(vectors[0])}-dimension vectors, expected "
                    f"{self.expected_dimensions} (EMBEDDING_DIMENSIONS)."
                )
            self._checked = True
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = [[float(x) for x in vec] for vec in self._model().embed(texts)]
        return self._verify(vectors)

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    # ONNX inference is CPU-bound; keep it off the event loop so retrieval and the API stay responsive.
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_documents, texts)

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.to_thread(self.embed_query, text)


def provision(model_name: str, cache_dir: str) -> int:
    """Download the model into ``cache_dir`` and return its vector size. Needs internet; see module docstring."""
    if get_settings().app.egress_enforce:
        raise LocalEmbeddingsUnavailableError(
            "Refusing to download while egress enforcement is on. Run this on a connected machine with "
            "EGRESS_ENFORCE=false, then copy the cache directory into the sandbox."
        )
    from fastembed import TextEmbedding

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    model = TextEmbedding(model_name=model_name, cache_dir=cache_dir)
    return len(next(iter(model.embed(["dimension probe"]))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m shared.local_embeddings")
    sub = parser.add_subparsers(dest="command", required=True)
    prov = sub.add_parser("provision", help="download the local embedding model (needs internet, EGRESS_ENFORCE=false)")
    llm = get_settings().llm
    prov.add_argument("--model", default=llm.embedding_local_model)
    prov.add_argument("--cache-dir", default=llm.embedding_local_cache_dir)
    args = parser.parse_args(argv)
    try:
        dim = provision(args.model, args.cache_dir)
    except LocalEmbeddingsUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"provisioned {args.model} into {args.cache_dir} ({dim}-dimension vectors)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
