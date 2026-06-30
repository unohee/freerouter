"""freerouter settings — loaded from environment variables (.env)."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings that control proxy behavior.

    Read from `.env` or process environment variables (case-insensitive keys).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenRouter auth / endpoint
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Routing behavior
    # TTL (seconds) before the free-model list is re-fetched.
    model_refresh_ttl: int = 600
    # Max number of free models to try as fallbacks within a single request.
    # Free models are frequently rate-limited or demand credits (402), so the
    # top few can fail in a row on a cold start — keep this generous.
    max_attempts: int = 8
    # Cooldown (seconds) to skip a model that returned 429 (rate limit).
    cooldown_seconds: int = 60
    # Upstream request timeout (seconds).
    request_timeout: float = 120.0

    # OpenRouter's recommended identification headers (optional) — shown in the
    # usage dashboard / rankings.
    http_referer: str = "https://github.com/unohee/freerouter"
    x_title: str = "freerouter"

    # Server binding
    host: str = "127.0.0.1"
    port: int = 8000


settings = Settings()
