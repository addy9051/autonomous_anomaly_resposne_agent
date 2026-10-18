"""
LLM factory for the sandboxed deployment.

Every model is served by a self-hosted OpenAI-compatible inference server (vLLM / Ollama)
inside the VPC. There is no external provider path: the endpoint is validated against the
egress policy, so pointing it at a public API fails closed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shared.config import get_settings
from shared.egress import check_url

if TYPE_CHECKING:
    from langchain_core.embeddings import Embeddings
    from langchain_core.language_models.chat_models import BaseChatModel


def get_chat_model(
    model_name: str | None = None,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    **kwargs: Any,  # noqa: ANN401
) -> BaseChatModel:
    """Return a chat model backed by the in-VPC inference server (or another server passed explicitly)."""
    settings = get_settings().llm
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        raise ImportError("langchain-openai (OpenAI-compatible client) is required: pip install langchain-openai") from None
    return ChatOpenAI(
        model=model_name or settings.default_model,
        base_url=check_url(base_url or settings.base_url),
        api_key=api_key or settings.api_key,
        temperature=temperature,
        max_tokens=max_tokens or 2048,
        timeout=settings.request_timeout_seconds,
        **kwargs,
    )


def get_embeddings() -> Embeddings:
    """Return an embeddings client backed by the in-VPC inference server."""
    settings = get_settings().llm
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=settings.embedding_model,
        base_url=check_url(settings.embeddings_url),
        api_key=settings.api_key,
        # `dimensions` is deliberately not sent: vLLM rejects it for non-Matryoshka models. The model's
        # native size must equal EMBEDDING_DIMENSIONS (the pgvector column size).
        check_embedding_ctx_length=False,
    )
