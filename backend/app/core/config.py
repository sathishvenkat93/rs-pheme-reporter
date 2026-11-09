"""Application settings, loaded from environment variables (prefix ``PHEME_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
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

    # The app connects as the non-owner role (row-level security applies to it); migrations
    # run as the owner. An empty migration URL means "same as database_url".
    database_url: str = "postgresql+psycopg://pheme_app:pheme_app@localhost:5432/pheme"
    migration_database_url: str = "postgresql+psycopg://pheme:pheme@localhost:5432/pheme"
    app_db_password: str = "pheme_app"  # noqa: S105 - dev default for the pheme_app role
    redis_url: str = "redis://localhost:6379/0"

    s3_endpoint: str = "localhost:8333"
    s3_access_key: str = "pheme"
    s3_secret_key: str = "pheme-secret"  # noqa: S105 - dev default, overridden by env
    s3_bucket: str = "pheme"
    s3_secure: bool = False

    max_upload_bytes: int = Field(default=200 * 1024 * 1024, gt=0)
    default_timezone: str = "UTC"
    local_cache_dir: Path = Path("/tmp/pheme-cache")  # noqa: S108 - dev default

    # Auth: "none" (fixed dev user; refused in production), "dev" (email login, signed
    # tokens) or "oidc" (bearer JWTs from an OIDC provider, verified against its JWKS).
    auth_mode: Literal["none", "dev", "oidc"] = "none"
    secret_key: str = "dev-only-secret-change-me-in-production-0000"  # noqa: S105 - dev default
    session_ttl_s: int = Field(default=7 * 86_400, gt=0)
    oidc_issuer: str = ""
    oidc_audience: str = ""
    oidc_workspace_claim: str = ""  # claim naming the workspace; default: the email domain

    dev_user_id: UUID = UUID("00000000-0000-0000-0000-000000000001")
    dev_workspace_id: UUID = UUID("00000000-0000-0000-0000-0000000000a1")

    queue_name: str = "pheme"

    query_timeout_s: float = Field(default=30.0, gt=0)
    query_row_cap: int = Field(default=10_000, gt=0)
    query_cache_ttl_s: int = Field(default=86_400, gt=0)

    # R4 scoring weights; redundancy and clutter are multipliers on their penalties.
    recommend_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "data_fit": 0.30,
            "intent": 0.25,
            "insight": 0.20,
            "perception": 0.15,
            "domain_prior": 0.10,
            "redundancy": 1.0,
            "clutter": 1.0,
        }
    )

    llm: str = "fake"  # "fake" (default, offline) or "anthropic"
    anthropic_api_key: str = ""
    model_main: str = "claude-sonnet-5-5"
    model_fast: str = "claude-haiku-5-5"
    live_llm: bool = False


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached)."""
    return Settings()
