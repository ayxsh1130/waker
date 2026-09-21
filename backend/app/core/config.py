from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://autopilot:change_me_local_only@postgres:5432/autopilot"
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str = ""
    broker_probe_url: str = "redis://broker-proxy:16379/0"
    frontend_url: str = "http://localhost:5173"
    backend_url: str = "http://localhost:8000"
    cors_allowed_origins: str = "http://localhost:5173"
    app_auth_enabled: bool = False
    admin_token: SecretStr = SecretStr("")
    cookie_secure: bool = False
    app_environment: Literal["development", "test", "production"] = "development"
    internal_token: SecretStr = SecretStr("")
    metrics_token_file: str = "/run/waker-metrics/token"
    internal_token_file: str = "/run/autopilot/control_token"
    dependency_url: str = "http://test-dependency:8080"
    worker_control_url: str = "http://worker-control:8090"
    proxy_control_url: str = "http://broker-proxy:8474"
    prometheus_url: str = "http://prometheus:9090"
    embedding_service_url: str = "http://retrieval:8081"
    embedding_provider: Literal["local", "disabled"] = "local"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    rag_max_results: int = Field(4, ge=1, le=10)
    llm_provider: Literal["none", "groq", "ollama", "compatible"] = "none"
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "llama-3.3-70b-versatile"
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "qwen3:8b"
    llm_base_url: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""
    llm_timeout_seconds: int = Field(60, ge=1, le=180)
    llm_temperature: float = Field(0, ge=0, le=2)
    llm_input_cost_per_million: float | None = None
    llm_output_cost_per_million: float | None = None
    agent_max_steps: int = Field(12, ge=2, le=30)
    agent_max_tool_calls: int = Field(10, ge=1, le=25)
    agent_max_reinvestigations: int = Field(2, ge=0, le=4)
    agent_max_context_chars: int = Field(32000, ge=8000, le=64000)
    agent_prompt_version: str = "v1"
    workflow_version: str = "v1"
    tool_timeout_seconds: int = Field(8, ge=1, le=30)
    github_token: SecretStr = SecretStr("")
    github_repository: str = ""
    local_repository_path: str = "/source/workers"
    remediation_mode: Literal["dry_run", "execute"] = "dry_run"
    remediation_min_confidence: float = Field(0.75, ge=0, le=1)
    remediation_cooldown_seconds: int = Field(60, ge=5)
    observation_seconds: int = Field(20, ge=5, le=120)
    worker_offline_seconds: int = Field(25, ge=10)
    backlog_threshold: int = Field(20, ge=5)
    failure_rate_threshold: float = Field(0.5, ge=0, le=1)
    latency_threshold_seconds: float = Field(8, ge=1, le=120)
    alert_min_completions: int = Field(5, ge=2, le=100)
    detection_window_seconds: int = Field(120, ge=10)
    auto_investigate: bool = True
    faults_enabled: bool = True
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    artifact_dir: str = "/data/artifacts"
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4318"
    software_revision: str = "unversioned"
    session_ttl_seconds: int = Field(28800, ge=300, le=86400)

    @model_validator(mode="after")
    def secure_deployment(self):
        if self.app_environment == "production" and not (self.app_auth_enabled and self.cookie_secure):
            raise ValueError("Production requires application authentication and HTTPS cookies")
        return self

    @property
    def control_token(self):
        path = Path(self.internal_token_file)
        return self.internal_token.get_secret_value() or (path.read_text().strip() if path.is_file() else "")

    @property
    def model_name(self):
        return {"groq": self.groq_model, "ollama": self.ollama_model}.get(self.llm_provider, self.llm_model)

    @property
    def llm_configured(self):
        return (
            bool(self.groq_api_key.get_secret_value())
            if self.llm_provider == "groq"
            else bool(self.ollama_model)
            if self.llm_provider == "ollama"
            else self.llm_provider == "compatible" and bool(self.llm_base_url and self.llm_model)
        )


@lru_cache
def settings():
    return Settings()
