"""Application settings (pydantic-settings)."""
from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote, urlparse

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "QAtestbuddy"
    app_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"
    env: str = "dev"

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    # Two ways to configure the database connection:
    #   1. Set DB_PASSWORD (recommended) — we build DATABASE_URL from
    #      SUPABASE_URL + DB_PASSWORD + DB_REGION. URL-encoding is handled.
    #   2. Set DATABASE_URL explicitly (advanced, e.g. self-hosted Postgres).
    db_password: str = ""
    db_region: str = "ap-northeast-1"
    db_pooler_host: str = ""   # leave empty to auto-build from region
    db_pooler_port: int = 6543
    database_url: str = ""

    @computed_field  # type: ignore[misc]
    @property
    def db_url(self) -> str:
        # Prefer an explicit, complete DATABASE_URL when provided.
        if self.database_url and "<" not in self.database_url:
            return self.database_url
        # Otherwise build from parts.
        if not self.supabase_url or not self.db_password:
            # Local dev fallback (won't actually connect to anything real).
            return "postgresql+asyncpg://postgres:postgres@localhost:5432/qa"
        host = urlparse(self.supabase_url).hostname or ""
        project_ref = host.split(".")[0] if host else ""
        encoded_pwd = quote(self.db_password, safe="")
        # Supabase pooler hostnames vary per project ("aws-0-..." for older
        # projects, "aws-1-..." for newer). Override via DB_POOLER_HOST.
        pooler_host = self.db_pooler_host or f"aws-1-{self.db_region}.pooler.supabase.com"
        return (
            f"postgresql+asyncpg://postgres.{project_ref}:{encoded_pwd}"
            f"@{pooler_host}:{self.db_pooler_port}/postgres"
        )

    app_encryption_key: str = ""

    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_from: str = "no-reply@localhost"

    sentry_dsn: str = ""
    logfire_token: str = ""

    allowed_origins: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
