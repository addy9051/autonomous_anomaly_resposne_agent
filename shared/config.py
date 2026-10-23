"""
Centralized configuration management using pydantic-settings.

Loads configuration from environment variables and .env file.
All agent configuration is validated at startup.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

# Langfuse disabled due to environment import issues
from pydantic import AliasChoices, BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).parent.parent


class LLMConfig(BaseSettings):
    """LLM provider configuration."""

    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    # Self-hosted, OpenAI-compatible inference server inside the VPC (vLLM / Ollama).
    # No external provider is supported: the sandbox has no internet egress.
    base_url: str = Field(default="http://localhost:8001/v1", validation_alias="LLM_BASE_URL")
    api_key: str = Field(default="not-needed", validation_alias="LLM_API_KEY")
    request_timeout_seconds: float = 60.0

    # Embeddings served by the same (or a sibling) in-VPC server.
    embedding_base_url: str = Field(default="", validation_alias="EMBEDDING_BASE_URL")
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768

    # Per-agent model routing (names of models served by the local server)
    default_model: str = "llama-3.1-8b-instruct"
    monitoring_agent_model: str = "llama-3.1-8b-instruct"
    diagnosis_agent_model: str = "llama-3.1-70b-instruct"
    action_agent_model: str = "llama-3.1-70b-instruct"
    feedback_agent_model: str = "llama-3.1-8b-instruct"
    judge_model: str = "qwen2.5-72b-instruct"  # must be a different provider than every agent model

    # The judge may live on its own server (it is a different model family); blank = same server as the agents.
    judge_base_url: str = Field(default="", validation_alias="JUDGE_BASE_URL")
    judge_api_key: str = Field(default="", validation_alias="JUDGE_API_KEY")

    @property
    def embeddings_url(self) -> str:
        return self.embedding_base_url or self.base_url


class DataConfig(BaseSettings):
    """Data infrastructure configuration."""

    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    # PostgreSQL / pgvector
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "agent_kb"
    postgres_user: str = "postgres"
    postgres_password: str = Field(
        default="",
        description="PostgreSQL password. Must be supplied externally via POSTGRES_PASSWORD environment variable.",
    )

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

    # Pub/Sub (Production replacement for Kafka)
    pubsub_project_id: str = ""
    pubsub_topic_prefix: str = "anomaly-events"

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

    @property
    def rag_dsn(self) -> str:
        """DSN for the RAG knowledge base — prefers Supabase Cloud, falls back to local."""
        if self.supabase_db_url and self.supabase_db_url != "postgresql://postgres:password@localhost:5432/agent_kb":
            return self.postgres_dsn  # never use a cloud DB in the sandbox
        return self.postgres_dsn


class AgentConfig(BaseSettings):
    """Agent behavior configuration."""

    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    anomaly_confidence_threshold: float = 0.75
    checkpointer_backend: Literal["memory", "postgres"] = Field(
        default="memory",
        description="LangGraph checkpointer for diagnosis state. 'postgres' survives restarts and resumes incidents",
    )
    max_tokens_per_incident: int = 50_000
    max_llm_retries: int = 3
    rag_top_k: int = 5
    rag_similarity_threshold: float = 0.75
    rag_candidates_per_leg: int = 50
    rag_rerank_enabled: bool = True
    rag_rerank_model: str = "ms-marco-TinyBERT-L-2-v2"
    rag_rerank_cache_dir: str = "models/flashrank"
    rag_rerank_weight: float = 1.0
    rag_bm25_backend: Literal["auto", "paradedb", "inprocess"] = Field(
        default="auto",
        description=(
            "BM25 implementation: 'paradedb' uses the pg_search index in Postgres, 'inprocess' scores the whole "
            "table in Python, 'auto' tries ParadeDB and falls back to in-process if pg_search is missing."
        ),
    )
    rag_exclude_untrusted: bool = Field(
        default=True,
        description="Never retrieve documents whose metadata trust_tier is 'untrusted' (poisoning control).",
    )
    rag_guard_threshold: float | None = Field(
        default=0.5,
        description=(
            "Exclude documents whose stored guard_score (ingest-time classifier) is >= this value. Unscored "
            "documents (NULL) are kept. Set to null to disable."
        ),
    )
    rag_guard_model: str = Field(
        default="jev-1.13.0", description="Jev model pinned for guard_score (jev-latest moves)."
    )
    rag_guard_on_ingest: bool = Field(
        default=False,
        description=(
            "Score each chunk with the hosted Jev guard during ingestion (needs TYPESAFE_API_KEY; chunk text leaves "
            "the machine). Scoring failures leave guard_score NULL; knowledge_base.score_guard backfills them."
        ),
    )
    embedding_chunk_size: int = 512
    embedding_chunk_overlap: int = 64
    stabilization_window_seconds: float = Field(
        default=30.0,
        description="Post-action stabilization window before verification in production (seconds)",
    )
    stabilization_window_seconds_dry_run: float = Field(
        default=0.1,
        description="Stabilization window for dry-run / simulation verification (seconds)",
    )
    auto_remediation_enabled: bool = Field(
        default=False,
        description="Allow the agent to execute allowlisted reversible actions on non-critical incidents",
    )
    auto_action_allowlist: str = Field(
        default="restart_unhealthy_pod,scale_replicas,clear_cache",
        description="Comma-separated reversible actions the agent may execute without approval",
    )
    auto_action_target_services: str = Field(
        default="",
        description="Comma-separated services where auto-remediation is permitted (empty = none; '*' = all)",
    )
    auto_action_max_per_hour: int = Field(default=3, description="Max auto-executed actions per service per hour")
    duplicate_window_minutes: int = Field(
        default=60, description="Window in which an identical fingerprint is treated as a duplicate episode"
    )


class IntegrationConfig(BaseSettings):
    """Third-party integration configuration."""

    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    # Slack (Supports both #channel-names and C012345678 IDs)
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_alert_channel: str = Field(
        default="", validation_alias=AliasChoices("SLACK_ALERT_CHANNEL_ID", "SLACK_ALERT_CHANNEL")
    )
    slack_approval_channel: str = Field(
        default="", validation_alias=AliasChoices("SLACK_APPROVAL_CHANNEL_ID", "SLACK_APPROVAL_CHANNEL")
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

    # LangSmith is hosted: off unless its endpoint passes the egress policy (see get_settings).
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "anomaly-agent"
    langchain_endpoint: str = "https://api.smith.langchain.com"

    # Telemetry Connector Mode ("simulator", "prometheus", "datadog", "splunk")
    telemetry_connector_mode: str = Field(
        default="simulator",
        validation_alias=AliasChoices("CONNECTOR_MODE", "TELEMETRY_CONNECTOR_MODE"),
    )

    # Combined live ingestion: comma-separated vendors (e.g. "prometheus,datadog,splunk").
    # Empty falls back to the single telemetry_connector_mode.
    telemetry_connector_modes: str = Field(
        default="",
        validation_alias=AliasChoices("CONNECTOR_MODES", "TELEMETRY_CONNECTOR_MODES"),
    )
    enable_live_ingestion: bool = False
    ingestion_poll_interval_seconds: float = 15.0
    ingestion_services: str = Field(default="", description="Comma-separated services to watch in live ingestion")
    ingestion_dry_run: bool = Field(default=True, description="Run escalated incidents in dry-run (no real actions)")
    ingestion_escalation_cooldown_seconds: float = 300.0
    ingestion_zscore_threshold: float = 3.0
    ingestion_min_samples: int = 8
    # JSON: {"prometheus": {"error_rate": "promql with {entity}"}, ...}; overrides built-in defaults
    telemetry_query_templates: str = ""

    # Prometheus optional auth
    prometheus_bearer_token: str = ""

    # Datadog API
    datadog_api_key: str = ""
    datadog_app_key: str = ""
    datadog_site: str = "datadoghq.com"

    # Splunk REST API
    splunk_host: str = "localhost"
    splunk_port: int = 8089
    splunk_token: str = ""
    splunk_username: str = "admin"
    splunk_password: str = ""
    # TLS verification stays on by default; set to false or to a CA bundle path for self-signed dev instances.
    splunk_verify_ssl: bool = True
    splunk_ca_bundle: str = ""

    # Deploy events (GitHub Deployments API). Empty repo disables the source.
    github_token: str = ""
    github_deploy_repo: str = ""  # "owner/repo"
    github_deploy_environment: str = "production"


class ObservabilityConfig(BaseSettings):
    """Observability and telemetry configuration."""

    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "anomaly-response-agent"

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = Field(
        default="http://localhost:3000", validation_alias=AliasChoices("LANGFUSE_BASE_URL", "LANGFUSE_HOST")
    )
    langfuse_enabled: bool = True  # Toggle for silencing noise in Lite Mode


class AppConfig(BaseSettings):
    """Application-level configuration."""

    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), extra="ignore")

    app_env: str = "production"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"  # noqa: S104
    api_port: int = 8000
    api_auth_token: str = Field(default="", description="Fallback API key for development when gateway is unavailable")
    trusted_identity_header: str = Field(
        default="X-Authenticated-User",
        description="Header set by API Gateway containing the authenticated caller identity",
    )
    api_gateway_stage_prefix: str = Field(
        default="",
        description="Optional API Gateway stage path prefix to strip from private integration requests",
    )
    api_gateway_secret: str = Field(
        default="",
        validation_alias=AliasChoices("API_GATEWAY_SECRET", "GATEWAY_SECRET"),
        description="Pre-shared secret that API Gateway must provide to authenticate gateway-injected identity headers",
    )
    gateway_secret_header: str = Field(
        default="X-Gateway-Secret",
        description="Header containing the pre-shared secret from API Gateway",
    )
    allow_anonymous_dev: bool = Field(
        default=False,
        description="Explicitly allow unauthenticated access in development when no auth token is configured",
    )
    require_gateway_auth: bool = Field(
        default=True,
        description="Require gateway-provided identity header in non-development environments",
    )
    enable_synthetic_heartbeat: bool = Field(
        default=False,
        description="Enable synthetic telemetry heartbeat. Auto-enabled in development if not explicitly set.",
    )
    egress_enforce: bool = Field(default=True, description="Fail closed on outbound calls outside the allowlist")
    egress_allowed_hosts: str = Field(
        default="", description="Comma-separated extra hosts (supports *.suffix), e.g. vendor SaaS endpoints"
    )
    egress_allowed_cidrs: str = Field(
        default="", description="Comma-separated extra CIDRs; private ranges are always allowed"
    )


class Settings(BaseModel):
    """Aggregate all configuration sections."""

    llm: LLMConfig = Field(default_factory=LLMConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    integrations: IntegrationConfig = Field(default_factory=IntegrationConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    app: AppConfig = Field(default_factory=AppConfig)

    @property
    def integration(self) -> IntegrationConfig:
        return self.integrations


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

    # --- Secret Manager CSI Driver Integration ---
    # Automatically override settings if secrets are mounted as files in /mnt/secrets
    secret_mount_path = Path("/mnt/secrets")
    if secret_mount_path.exists():
        # Map filenames to setting attributes and environment variables
        secret_map = {
            "llm-api-key": ("llm", "api_key", "LLM_API_KEY"),
            "pagerduty-api-key": ("integrations", "pagerduty_api_key", "PAGERDUTY_API_KEY"),
            "slack-bot-token": ("integrations", "slack_bot_token", "SLACK_BOT_TOKEN"),
            "n8n-api-key": ("integrations", "n8n_api_key", "N8N_API_KEY"),
            "postgres-password": ("data", "postgres_password", "POSTGRES_PASSWORD"),
        }
        for filename, (section, attr, env_var) in secret_map.items():
            file_path = secret_mount_path / filename
            if file_path.exists():
                secret_value = file_path.read_text().strip()
                if secret_value:
                    # Update settings object
                    section_config = getattr(settings, section)
                    setattr(section_config, attr, secret_value)
                    # Synchronize to environment for SDK compatibility
                    os.environ[env_var] = secret_value

    # Hosted LangSmith would ship every prompt and response outside the sandbox. LangChain reads these
    # from the process environment (which a dotenv loader may have filled), so enforce the policy there.
    from shared.egress import EgressViolationError, check_url

    try:
        check_url(settings.integrations.langchain_endpoint, settings.app)
        langsmith_allowed = True
    except EgressViolationError:
        langsmith_allowed = False
    if not (langsmith_allowed and settings.integrations.langchain_tracing_v2):
        for var in ("LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING", "LANGSMITH_TRACING"):
            os.environ[var] = "false"
        for var in ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY"):
            os.environ.pop(var, None)

    return settings
