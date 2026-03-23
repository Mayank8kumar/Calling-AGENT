"""
# Pydantic Settings — centralized configuration
# Loads ALL config from environment variables / .env file
# Includes: app settings, database, redis, JWT, all provider API keys
# Singleton via @lru_cache — called everywhere as get_settings()
# Key properties: is_production, is_development
"""
"""
Application configuration loaded from environment variables.
All secrets and provider keys are centralized here.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Central configuration — every setting comes from env vars or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_name: str = "voice-agent-platform"
    app_env: Environment = Environment.DEVELOPMENT
    debug: bool = True
    log_level: str = "INFO"
    secret_key: str = Field(min_length=32)
    api_v1_prefix: str = "/api/v1"
    allowed_origins: list[str] = ["http://localhost:3000"]
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    workers: int = 1

    # --- Database ---
    database_url: str
    database_pool_size: int = 20
    database_max_overflow: int = 10
    database_echo: bool = False

    # --- Redis ---
    redis_url: str = "redis://localhost:6379/0"
    redis_cache_db: int = 1
    redis_celery_db: int = 2

    # --- Celery ---
    celery_broker_url: str = "redis://localhost:6379/2"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_concurrency: int = 4

    # --- JWT ---
    jwt_secret_key: str = Field(min_length=16)
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_days: int = 7

    # --- Twilio ---
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    twilio_webhook_base_url: str = ""

    # --- Plivo ---
    plivo_auth_id: str = ""
    plivo_auth_token: str = ""
    plivo_phone_number: str = ""

    # --- Deepgram (STT) ---
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-3"
    deepgram_language: str = "multi"

    # --- OpenAI (LLM) ---
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_max_tokens: int = 200
    openai_temperature: float = 0.7

    # --- Anthropic (LLM fallback) ---
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"

    # --- Cartesia (TTS) ---
    cartesia_api_key: str = ""
    cartesia_model: str = "sonic-3"
    cartesia_voice_id: str = ""
    cartesia_language: str = "en"
    cartesia_sample_rate: int = 24000

    # --- ElevenLabs (TTS fallback) ---
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_flash_v2_5"

    # --- Object Storage ---
    s3_endpoint_url: str | None = None  # None = use real AWS S3
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_bucket_recordings: str = "voice-recordings"
    s3_bucket_exports: str = "voice-exports"
    s3_region: str = "us-east-1"

    # --- Monitoring ---
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "voice-agent-platform"
    prometheus_port: int = 9090

    # --- Encryption ---
    encryption_key: str = ""

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    @property
    def is_production(self) -> bool:
        return self.app_env == Environment.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.app_env == Environment.DEVELOPMENT


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings instance — cached after first call."""
    return Settings()  # type: ignore[call-arg]