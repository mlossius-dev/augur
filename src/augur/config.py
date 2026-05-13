"""
Centralised configuration for Augur.

All settings are read from environment variables (or a .env file in development).
Pydantic Settings enforces types and raises at startup if required values are absent,
so misconfigured deployments fail loudly at boot rather than at first use.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str
    # Minimum and maximum connections in the async pool
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10

    # ── OpenRouter ────────────────────────────────────────────────────────────
    # Main key — pipeline stages (extraction, anchoring, disconfirmation)
    openrouter_api_key: str
    # Free-tier key — conversation layer only; must be restricted on OpenRouter dashboard
    openrouter_free_tier_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # ── Langfuse ──────────────────────────────────────────────────────────────
    langfuse_host: str
    langfuse_public_key: str
    langfuse_secret_key: str

    # ── SearXNG ───────────────────────────────────────────────────────────────
    searxng_url: str = "http://searxng:8080"

    # ── Object storage (off-host backups) ────────────────────────────────────
    object_storage_endpoint: str = ""
    object_storage_bucket: str = "augur-backups"
    object_storage_access_key: str = ""
    object_storage_secret_key: str = ""
    object_storage_region: str = "eu-central"

    # ── Cost / budget ─────────────────────────────────────────────────────────
    # Daily spend cap for the main pipeline key; enforced at the call site.
    daily_llm_budget_usd: Decimal = Decimal("5.00")

    # ── Ingestion ─────────────────────────────────────────────────────────────
    # Local filesystem archive root for raw payloads
    payload_archive_root: str = "/data/augur/payloads"
    # Path to the sources.yaml registry; defaults to config/sources.yaml in repo
    sources_config_path: str = ""

    # ── Application ───────────────────────────────────────────────────────────
    augur_env: str = "development"
    log_level: str = "INFO"
    # "text" for development console, "json" for production/VPS
    log_format: str = "json"
    # Whether to start the APScheduler in this process
    enable_scheduler: bool = True

    @field_validator("log_level")
    @classmethod
    def normalise_log_level(cls, v: str) -> str:
        normalised = v.upper()
        if normalised not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Invalid LOG_LEVEL: {v}")
        return normalised

    @field_validator("log_format")
    @classmethod
    def normalise_log_format(cls, v: str) -> str:
        if v.lower() not in {"text", "json"}:
            raise ValueError(f"LOG_FORMAT must be 'text' or 'json', got: {v}")
        return v.lower()

    @model_validator(mode="after")
    def warn_if_object_storage_incomplete(self) -> Settings:
        has_endpoint = bool(self.object_storage_endpoint)
        has_keys = bool(self.object_storage_access_key and self.object_storage_secret_key)
        if has_endpoint and not has_keys:
            import warnings
            warnings.warn(
                "OBJECT_STORAGE_ENDPOINT is set but access/secret keys are missing. "
                "Off-host backups will not work.",
                stacklevel=2,
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.augur_env.lower() == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings. Reads .env on first call."""
    return Settings()
