"""
Serverless Pay-Per-Token LLM & Embedding Factory.
Zero fixed GPU cost; scales dynamically from $0 to millions of tokens.
Compatible with: OpenAI (gpt-4o-mini), OpenRouter (DeepSeek-V3, Gemini Flash), and Ollama/vLLM.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from core.config import get_settings
from core.utils import get_logger

logger = get_logger("llm_factory")


def get_chat_model(
    model_name: str | None = None,
    temperature: float = 0.1,
    max_tokens: int = 2048,
    **kwargs: Any,
) -> BaseChatModel:
    """Return a configured LangChain ChatModel backed by pay-per-token API."""
    settings = get_settings()

    api_key = settings.llm_api_key or "sk-dummy-key"
    base_url = settings.llm_base_url
    model = model_name or settings.llm_model

    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
        **kwargs,
    )


class LocalFastEmbeddings(Embeddings):
    """Zero-cost local embeddings using FastEmbed (ONNX runtime on CPU, zero API keys)."""

    _cached_instance = None

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding
        self._model = TextEmbedding(model_name=model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vec.tolist() for vec in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(self._model.embed([text])).tolist()

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


@lru_cache(maxsize=1)
def get_embeddings() -> Embeddings:
    """Return embeddings client: zero-cost local FastEmbed by default, or external API if configured."""
    settings = get_settings()

    if settings.embedding_provider == "openai" and settings.embedding_api_key:
        return OpenAIEmbeddings(
            model=settings.embedding_model,
            base_url=settings.effective_embedding_url,
            api_key=settings.effective_embedding_key,
            dimensions=settings.embedding_dimensions,
            check_embedding_ctx_length=False,
        )

    # 100% free local CPU embeddings with FastEmbed (Zero API key / zero quota)
    return LocalFastEmbeddings(model_name=settings.embedding_model)

