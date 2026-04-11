"""
LLM Factory — unified interface for multi-provider LLM support.

Routes model requests to the appropriate provider (OpenAI, Anthropic, Google)
based on the model name prefix and handles credential mapping.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI


from shared.config import get_settings

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


def get_chat_model(
    model_name: str,
    temperature: float = 0.1,
    max_tokens: int | None = None,
    **kwargs: Any,  # noqa: ANN401
) -> BaseChatModel:
    """
    Factory function to get a LangChain chat model based on the model name.

    Args:
        model_name: The name of the model (e.g., 'gpt-4o', 'claude-3-5-sonnet-latest')
        temperature: Sampling temperature
        max_tokens: Max tokens to generate
        **kwargs: Additional provider-specific arguments

    Returns:
        An instance of a LangChain BaseChatModel.
    """
    settings = get_settings()



    model_lower = model_name.lower()

    # 1. Anthropic (Claude)
    if model_lower.startswith("claude-"):
        return ChatAnthropic(
            model=model_name,
            anthropic_api_key=settings.llm.anthropic_api_key,
            temperature=temperature,
            max_tokens_to_sample=max_tokens or 2048,
            **kwargs,
        )

    # 2. Google (Gemini)
    elif model_lower.startswith("gemini-"):
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.llm.google_application_credentials or "", # Prioritize key if set
            temperature=temperature,
            max_output_tokens=max_tokens or 2048,
            **kwargs,
        )

    # 3. OpenAI (Default)
    else:
        return ChatOpenAI(
            model=model_name,
            api_key=settings.llm.openai_api_key,
            temperature=temperature,
            max_tokens=max_tokens or 2048,
            **kwargs,
        )
