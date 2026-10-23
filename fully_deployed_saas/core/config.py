"""
SaaS Configuration Management.
Validates all environment variables for zero-idle-cost production deployment.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parent.parent


class SaaSConfig(BaseSettings):
    """Central settings for the multi-tenant SaaS application."""

    model_config = SettingsConfigDict(
        env_file=(str(PROJECT_DIR.parent / ".env"), str(PROJECT_DIR / ".env"), str(PROJECT_DIR / ".env.local")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ─── Environment & Runtime ───
    app_env: Literal["development", "staging", "production"] = "development"
    api_title: str = "Autonomous SRE Intelligence SaaS"
    api_version: str = "1.0.0"
    cors_origins: str = "http://localhost:3000,http://localhost:3010,http://127.0.0.1:3000,*"

    # ─── Database (PostgreSQL + pgvector on Neon Serverless) ───
    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/agent_kb",
        validation_alias="DATABASE_URL",
        description="Pooled PostgreSQL DSN for application queries",
    )
    database_url_unpooled: str = Field(
        default="",
        validation_alias="DATABASE_URL_UNPOOLED",
        description="Unpooled PostgreSQL DSN for DDL and migrations",
    )
    neon_branch: str = Field(
        default="production",
        validation_alias="NEON_BRANCH",
        description="Active Neon Git-like database branch",
    )
    db_pool_min: int = 1
    db_pool_max: int = 10

    # ─── Security & Encryption ───
    # Master key for encrypting customer telemetry credentials (Fernet 32-byte base64)
    encryption_master_key: str = Field(
        default="e24zOHRzNHRyeHh5cndxOG5qNnVyMGV0cG1iN3Jpd3g=",
        validation_alias="ENCRYPTION_MASTER_KEY",
        description="Fernet URL-safe base64-encoded 32-byte key",
    )
    jwt_secret: str = Field(
        default="dev-insecure-jwt-secret-replace-in-production",
        validation_alias="JWT_SECRET",
    )

    # ─── Serverless Pay-per-Token LLM (Zero GPU Cost) ───
    # Default: OpenAI, OpenRouter, or DeepSeek
    llm_provider: Literal["openai", "openrouter", "deepseek", "gemini", "custom"] = "openai"
    llm_base_url: str = Field(default="https://api.openai.com/v1", validation_alias="LLM_BASE_URL")
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "OPENAI_API_KEY"),
    )
    llm_model: str = Field(default="gpt-4o-mini", validation_alias="LLM_MODEL")
    llm_timeout_seconds: float = 45.0

    # ─── Embeddings (Pay-per-Token) ───
    embedding_base_url: str = Field(default="", validation_alias="EMBEDDING_BASE_URL")
    embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("EMBEDDING_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"),
    )
    embedding_model: str = Field(default="text-embedding-3-small", validation_alias="EMBEDDING_MODEL")
    embedding_dimensions: int = 1536

    # ─── RAG & Search ───
    rag_top_k: int = 5
    rag_similarity_threshold: float = 0.70

    # ─── Governance & Autonomous Remediation ───
    auto_remediation_enabled: bool = False
    auto_action_max_per_hour: int = 5
    auto_action_allowlist: str = "restart_unhealthy_pod,scale_replicas,clear_cache"

    # ─── Optional Free Tracing (Langfuse Cloud) ───
    langfuse_public_key: str = Field(default="", validation_alias="LANGFUSE_PUBLIC_KEY")
    langfuse_secret_key: str = Field(default="", validation_alias="LANGFUSE_SECRET_KEY")
    langfuse_host: str = Field(default="https://cloud.langfuse.com", validation_alias="LANGFUSE_HOST")

    @property
    def effective_embedding_url(self) -> str:
        return self.embedding_base_url or self.llm_base_url

    @property
    def effective_embedding_key(self) -> str:
        return self.embedding_api_key or self.llm_api_key


@lru_cache(maxsize=1)
def get_settings() -> SaaSConfig:
    """Return cached SaaS settings instance."""
    return SaaSConfig()
