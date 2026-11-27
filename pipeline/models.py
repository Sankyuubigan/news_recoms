from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ContentType = Literal["telegram", "article", "youtube"]
ContentKind = Literal["news", "repos"]
ItemStatus = Literal["pending_ai", "inbox", "saved", "archived", "rejected"]


# ---------------------------------------------------------------- raw parsing


class RawPost(BaseModel):
    """Пост, извлечённый парсером до AI-обработки."""

    source_identifier: str
    source_name: str
    kind: ContentKind = "news"
    external_id: str | None = None
    url: str
    repo_url: str | None = None
    content_type: ContentType
    title_orig: str
    content_orig: str
    media_url: str | None = None
    published_at: str
    original_lang: str = "ru"

    @field_validator("title_orig", "content_orig")
    @classmethod
    def _strip(cls, v: str) -> str:
        return re.sub(r"\s+", " ", v or "").strip()


class BackfillConfig(BaseModel):
    """Параметры порционного разбора истории канала."""

    enabled: bool = False
    stop_at_id: int | None = None
    remaining: int | None = None
    cursor_id: int | None = None
    batch_limit: int = 80
    processed_total: int = 0


class SourceConfig(BaseModel):
    id: int
    type: Literal["telegram", "rss", "youtube"]
    kind: ContentKind = "news"
    name: str
    identifier: str
    category_hint: str | None = ""
    fetch_full_text: int = 0
    enabled: int = 1
    backfill: BackfillConfig | None = None


class SettingsBundle(BaseModel):
    manual_rules: str = ""
    learned_profile: str = ""
    repo_rules: str = ""
    repo_learned_profile: str = ""
    ad_regex_blacklist: str = ""
    min_inbox_score: float = 6.5
    min_repos_score: float = 6.5
    telegram_alert_score: float = 9.0
    # Порог для старта низкий: иначе первый профиль не соберётся неделями.
    # Дальше learner сам поднимает планку до LEARN_THRESHOLD_STEADY.
    learn_feedback_threshold: int = 5
    youtube_deep_summary_enabled: bool = True
    youtube_deep_summary_min_score: float = 7.5


class Bootstrap(BaseModel):
    sources: list[SourceConfig] = Field(default_factory=list)
    settings: dict[str, str] = Field(default_factory=dict)
    recent_urls: list[str] = Field(default_factory=list)
    recent_hashes: list[str] = Field(default_factory=list)
    recent_clusters: list[dict] = Field(default_factory=list)
    pending_ai_items: list[dict] = Field(default_factory=list)

    def bundle(self) -> SettingsBundle:
        s = self.settings

        def _f(key: str, default: float) -> float:
            try:
                return float(s.get(key, default))
            except (TypeError, ValueError):
                return default

        def _b(key: str, default: bool) -> bool:
            raw = s.get(key)
            if raw is None:
                return default
            return str(raw).strip().lower() in {"1", "true", "yes", "on"}

        return SettingsBundle(
            manual_rules=s.get("manual_rules", ""),
            learned_profile=s.get("learned_profile", ""),
            repo_rules=s.get("repo_rules", ""),
            repo_learned_profile=s.get("repo_learned_profile", ""),
            ad_regex_blacklist=s.get("ad_regex_blacklist", ""),
            min_inbox_score=_f("min_inbox_score", 6.5),
            min_repos_score=_f("min_repos_score", 6.5),
            telegram_alert_score=_f("telegram_alert_score", 9.0),
            learn_feedback_threshold=int(_f("learn_feedback_threshold", 5)),
            youtube_deep_summary_enabled=_b("youtube_deep_summary_enabled", True),
            youtube_deep_summary_min_score=_f("youtube_deep_summary_min_score", 7.5),
        )


# ------------------------------------------------------------- LLM contracts


class LLMResultItem(BaseModel):
    """Один элемент строгого JSON-ответа LLM."""

    temp_id: str
    is_ad: bool = False
    ad_reason: str | None = None
    score: float = 0.0
    category: str = "Общее"
    tags: list[str] = Field(default_factory=list)
    title_ru: str = ""
    summary_ru: str = ""
    key_points_ru: list[str] = Field(default_factory=list)
    ai_reason: str = ""
    cluster_slug: str | None = None
    repo_url: str | None = None
    interface_type: str | None = None

    @field_validator("score")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(10.0, float(v)))

    @field_validator("tags", "key_points_ru", mode="before")
    @classmethod
    def _coerce_list(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
        return []

    @field_validator("cluster_slug")
    @classmethod
    def _slug(cls, v: str | None) -> str | None:
        if not v:
            return None
        s = re.sub(r"[^a-z0-9-]+", "-", str(v).strip().lower()).strip("-")
        return s[:80] or None


class LLMResponse(BaseModel):
    results: list[LLMResultItem] = Field(default_factory=list)


class ProfileUpdateResponse(BaseModel):
    updated_learned_profile: str = ""
    changelog: str = ""


# ------------------------------------------------------------ ingest payload


class IngestItem(BaseModel):
    source_identifier: str
    kind: ContentKind = "news"
    external_id: str | None = None
    url: str
    repo_url: str | None = None
    interface_type: str | None = None
    content_hash: str
    content_type: ContentType
    original_lang: str = "ru"
    title_orig: str
    content_orig: str
    media_url: str | None = None
    title_ru: str | None = None
    summary_ru: str | None = None
    key_points: list[str] = Field(default_factory=list)
    category: str = "Общее"
    tags: list[str] = Field(default_factory=list)
    ai_score: float | None = None
    ai_reason: str | None = None
    is_ad: bool = False
    ad_reason: str | None = None
    cluster_slug: str | None = None
    merged_sources: list[dict] = Field(default_factory=list)
    status: ItemStatus = "inbox"
    published_at: str


class RunStats(BaseModel):
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    fetched_count: int = 0
    ad_blocked_count: int = 0
    dedup_skipped_count: int = 0
    ai_processed_count: int = 0
    inbox_added_count: int = 0
    rejected_count: int = 0
    pending_ai_count: int = 0
    provider_stats: dict[str, dict[str, int]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    status: Literal["success", "partial", "failed"] = "success"
