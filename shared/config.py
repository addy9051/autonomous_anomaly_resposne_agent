"""
Centralized configuration management using pydantic-settings.

Loads configuration from environment variables and .env file.
All agent configuration is validated at startup.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from langfuse.decorators import langfuse_context
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent


class LLMConfig(BaseSettings):
    """LLM provider configuration."""
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # Google / Vertex AI
    google_application_credentials: str | None = None
    google_project_id: str = ""
    google_region: str = "us-central1"
    vertex_ai_model: str = "gemini-2.5-flash-lite"

    # Anthropic
    anthropic_api_key: str = ""

    # Per-agent model routing
    monitoring_agent_model: str = "gpt-4o-mini"
    diagnosis_agent_model: str = "gpt-4o"
    action_agent_model: str = "gpt-4o"
    feedback_agent_model: str = "gpt-4o-mini"


class DataConfig(BaseSettings):
    """Data infrastructure configuration."""
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    # PostgreSQL / pgvector
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "agent_kb"
    postgres_user: str = "postgres"
    postgres_password: str = "agent_secret_2024"  # noqa: S105

    # Supabase (Cloud Alternative to local Postgres)
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    supabase_db_url: str = ""

    # Feature Store / State
    redis_url: str = "redis://localhost:6379/0"

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_schema_registry_url: str = "http://localhost:8081"

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def async_postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


class AgentConfig(BaseSettings):
    """Agent behavior configuration."""
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    anomaly_confidence_threshold: float = 0.75
    max_tokens_per_incident: int = 50_000
    max_llm_retries: int = 3
    rag_top_k: int = 5
    rag_similarity_threshold: float = 0.75
    embedding_chunk_size: int = 512
    embedding_chunk_overlap: int = 64


class IntegrationConfig(BaseSettings):
    """Third-party integration configuration."""
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    # Slack (Supports both #channel-names and C012345678 IDs)
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_alert_channel: str = Field(
        default="",
        validation_alias=AliasChoices("SLACK_ALERT_CHANNEL_ID", "SLACK_ALERT_CHANNEL")
    )
    slack_approval_channel: str = Field(
        default="",
        validation_alias=AliasChoices("SLACK_APPROVAL_CHANNEL_ID", "SLACK_APPROVAL_CHANNEL")
    )

    # PagerDuty
    pagerduty_api_key: str = ""
    pagerduty_service_id: str = ""
    pagerduty_user_email: str = ""

    # N8n
    n8n_base_url: str = "http://localhost:5678"
    n8n_api_key: str = ""

    # Prometheus
    prometheus_url: str = "http://localhost:9090"

    # Grafana (Supports API Keys or modern Service Account Tokens)
    grafana_url: str = "http://localhost:3000"
    grafana_token: str = Field("", alias="grafana_api_key")

    # LangSmith
    langchain_tracing_v2: bool = True
    langchain_api_key: str = ""
    langchain_project: str = "anomaly-agent"
    langchain_endpoint: str = "https://api.smith.langchain.com"


class ObservabilityConfig(BaseSettings):
    """Observability and telemetry configuration."""
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "anomaly-response-agent"

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = Field(
        default="http://localhost:3000",
        validation_alias=AliasChoices("LANGFUSE_BASE_URL", "LANGFUSE_HOST")
    )
    langfuse_enabled: bool = True  # Toggle for silencing noise in Lite Mode


class AppConfig(BaseSettings):
    """Application-level configuration."""
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"  # noqa: S104
    api_port: int = 8000


class Settings(BaseModel):
    """Aggregate all configuration sections."""
    llm: LLMConfig = Field(default_factory=LLMConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    integrations: IntegrationConfig = Field(default_factory=IntegrationConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    app: AppConfig = Field(default_factory=AppConfig)



@lru_cache
def get_settings() -> Settings:
    """Returns a cached instance of the settings."""
    settings = Settings()

    # Final hardening: Explicitly sync Langfuse keys to os.environ
    # This prevents race conditions where decorators initialize before settings are loaded
    if settings.observability.langfuse_public_key:
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.observability.langfuse_public_key
    if settings.observability.langfuse_secret_key:
        os.environ["LANGFUSE_SECRET_KEY"] = settings.observability.langfuse_secret_key
    if settings.observability.langfuse_host:
        os.environ["LANGFUSE_HOST"] = settings.observability.langfuse_host

    # SILENCE: Ensure global context respects the enabled toggle
    if not settings.observability.langfuse_enabled:
        langfuse_context.configure(enabled=False)

    return settings
