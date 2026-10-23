"""
Serverless Pay-Per-Token LLM & Embedding Factory.
Zero fixed GPU cost; scales dynamically from $0 to millions of tokens.
Compatible with: OpenAI (gpt-4o-mini), OpenRouter (DeepSeek-V3, Gemini Flash), and Ollama/vLLM.
"""

from __future__ import annotations

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


def get_embeddings() -> Embeddings:
    """Return configured OpenAI-compatible embeddings client."""
    settings = get_settings()

    api_key = settings.effective_embedding_key or "sk-dummy-key"
    base_url = settings.effective_embedding_url
    model = settings.embedding_model

    return OpenAIEmbeddings(
        model=model,
        base_url=base_url,
        api_key=api_key,
        dimensions=settings.embedding_dimensions,
        check_embedding_ctx_length=False,
    )
