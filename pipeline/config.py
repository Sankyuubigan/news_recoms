from __future__ import annotations

import argparse
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATA_DIR = "../data_repo"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Хранилище (ветка `data`, чекаутится в локальную папку) ---
    data_dir: str = Field(default=DEFAULT_DATA_DIR, alias="DATA_DIR")

    # --- LLM providers (JSON array, см. .env.example) ---
    llm_providers_json: str = Field(default="[]", alias="LLM_PROVIDERS_JSON")

    # --- YouTube deep summary (опционально) ---
    gemini_video_api_key: str = Field(default="", alias="GEMINI_VIDEO_API_KEY")
    gemini_video_model: str = Field(default="gemini-3.5-flash", alias="GEMINI_VIDEO_MODEL")
    youtube_max_deep_per_run: int = Field(default=3, alias="YOUTUBE_MAX_DEEP_PER_RUN")

    # --- Telegram alerts (опционально) ---
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    # --- Поведение ---
    http_timeout_sec: float = Field(default=15.0, alias="HTTP_TIMEOUT_SEC")
    max_items_per_source: int = Field(default=25, alias="MAX_ITEMS_PER_SOURCE")
    max_ai_items_per_run: int = Field(default=160, alias="MAX_AI_ITEMS_PER_RUN")
    max_pending_before_backfill_pause: int = Field(default=120, alias="MAX_PENDING_BEFORE_BACKFILL_PAUSE")
    dry_run: bool = Field(default=False, alias="DRY_RUN")

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NewsRecoms ingest pipeline")
    parser.add_argument(
        "--data-dir",
        default=None,
        help=f"Каталог с feed.json / user_state.json / config.json / runs.json (по умолчанию {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ничего не писать в файлы (только лог)",
    )
    return parser.parse_args(argv)


def apply_args(args: argparse.Namespace) -> None:
    """Переносит CLI-аргументы в переменные окружения, чтобы их подхватил Settings."""
    if args.data_dir:
        os.environ["DATA_DIR"] = str(args.data_dir)
    if getattr(args, "dry_run", False):
        os.environ["DRY_RUN"] = "true"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}