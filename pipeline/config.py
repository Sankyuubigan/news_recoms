from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Worker API ---
    worker_base_url: str = Field(default="", alias="WORKER_BASE_URL")
    ingest_secret: str = Field(default="", alias="INGEST_SECRET")

    # --- LLM providers (JSON array, см. .env.example) ---
    llm_providers_json: str = Field(default="[]", alias="LLM_PROVIDERS_JSON")

    # --- YouTube deep summary (опционально) ---
    gemini_video_api_key: str = Field(default="", alias="GEMINI_VIDEO_API_KEY")
    gemini_video_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_VIDEO_MODEL")
    youtube_max_deep_per_run: int = Field(default=3, alias="YOUTUBE_MAX_DEEP_PER_RUN")

    # --- Telegram alerts (опционально) ---
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # --- Поведение ---
    http_timeout_sec: float = Field(default=15.0, alias="HTTP_TIMEOUT_SEC")
    max_items_per_source: int = Field(default=25, alias="MAX_ITEMS_PER_SOURCE")
    dry_run: bool = Field(default=False, alias="DRY_RUN")

    @property
    def api_base(self) -> str:
        return self.worker_base_url.rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
