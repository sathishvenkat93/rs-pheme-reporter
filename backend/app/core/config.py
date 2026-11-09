"""Application settings, loaded from environment variables (prefix ``PHEME_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """All runtime configuration. Every field is listed in ``.env.example``."""

    model_config = SettingsConfigDict(
        env_prefix="PHEME_",
        env_file=(_REPO_ROOT / ".env", ".env"),
        extra="ignore",
    )

    env: str = "dev"
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://pheme:pheme@localhost:5432/pheme"
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint: str = "localhost:8333"
    s3_access_key: str = "pheme"
    s3_secret_key: str = "pheme-secret"  # noqa: S105 - dev default, overridden by env
    s3_bucket: str = "pheme"
    s3_secure: bool = False

    max_upload_bytes: int = Field(default=200 * 1024 * 1024, gt=0)
    default_timezone: str = "UTC"
    local_cache_dir: Path = Path("/tmp/pheme-cache")  # noqa: S108 - dev default

    dev_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    dev_workspace_id: UUID = UUID("00000000-0000-0000-0000-0000000000a1")

    queue_name: str = "pheme"

    query_timeout_s: float = Field(default=30.0, gt=0)
    query_row_cap: int = Field(default=10_000, gt=0)
    query_cache_ttl_s: int = Field(default=86_400, gt=0)

    anthropic_api_key: str = ""
    model_main: str = "claude-sonnet-5-5"
    model_fast: str = "claude-haiku-5-5"
    live_llm: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached)."""
    return Settings()
