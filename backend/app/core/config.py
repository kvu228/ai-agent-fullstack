"""Application settings, loaded from environment variables and `.env`."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- App -------------------------------------------------------------------------
    app_name: str = "Document Assistant API"
    environment: Literal["development", "staging", "production", "test"] = "development"
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    # Origins allowed by CORS *and* by the CSRF origin check (see app/api/deps.py).
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ---- Database (AWS RDS for PostgreSQL in production) -----------------------------
    database_url: str = "postgresql+psycopg://app:app@localhost:5433/app"
    db_pool_size: int = 5
    db_echo: bool = False

    # ---- Auth ------------------------------------------------------------------------
    jwt_secret_key: SecretStr = SecretStr(_DEV_JWT_SECRET)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 15
    refresh_token_ttl_days: int = 7
    # Secure cookies are only sent over HTTPS: must be true in production.
    cookie_secure: bool = False

    # ---- Blob storage (AWS S3; Floci locally) ----------------------------------------
    s3_bucket: str = "documents"
    s3_endpoint_url: str | None = "http://localhost:4566"  # None -> real AWS
    # Endpoint the *browser* uses for presigned URLs (differs inside Docker networks).
    s3_public_endpoint_url: str | None = None
    aws_region: str = "us-east-1"
    aws_access_key_id: SecretStr | None = SecretStr("test")
    aws_secret_access_key: SecretStr | None = SecretStr("test")
    max_upload_mb: int = 20

    # ---- Vector store (ChromaDB) -----------------------------------------------------
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_collection: str = "documents"

    # ---- LLM / RAG -------------------------------------------------------------------
    llm_model: str = "anthropic:claude-sonnet-5-5"
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    embedding_model: str = "openai:text-embedding-3-small"
    chunk_size: int = 1000
    chunk_overlap: int = 150
    retrieval_k: int = 5

    # ---- LangSmith tracing -----------------------------------------------------------
    # LangSmith reads os.environ, not Settings: see app/core/tracing.py.
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "AI Agent"

    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def checkpointer_conninfo(self) -> str:
        """LangGraph's Postgres checkpointer wants a plain libpq URL (no `+psycopg`)."""
        return self.database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    @field_validator("jwt_secret_key")
    @classmethod
    def _check_secret_length(cls, value: SecretStr) -> SecretStr:
        # HS256 needs >= 256 bits of key material (RFC 7518 §3.2).
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def _check_production_safety(self) -> Self:
        # Fail fast at startup instead of running production with dev defaults.
        if self.is_production:
            if self.jwt_secret_key.get_secret_value() == _DEV_JWT_SECRET:
                raise ValueError("JWT_SECRET_KEY must be set in production")
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (read env only once)."""
    return Settings()
