from __future__ import annotations

import json
import logging

from config import get_settings
from llm.prompts import YOUTUBE_SUMMARY_PROMPT
from models import RawPost

log = logging.getLogger(__name__)


class YouTubeDeepSummarizer:
    """Глубокое саммари YouTube-видео напрямую через google-genai (без yt-dlp)."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        self.api_key = api_key
        self.model = model

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def summarize(self, url: str) -> tuple[str, list[str]] | None:
        """Возвращает (summary_ru, key_points_ru) или None при любой ошибке."""
        if not self.enabled:
            return None
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)
            response = client.models.generate_content(
                model=self.model,
                contents=types.Content(
                    parts=[
                        types.Part(file_data=types.FileData(file_uri=url, mime_type="video/*")),
                        types.Part(text=YOUTUBE_SUMMARY_PROMPT),
                    ]
                ),
            )
            text = getattr(response, "text", None)
            if not text:
                return None

            from llm.router import extract_json

            data = extract_json(text)
            summary = str(data.get("summary_ru") or "").strip()
            points = [str(p) for p in (data.get("key_points_ru") or []) if p]
            if not summary:
                return None
            return summary, points
        except Exception as e:  # noqa: BLE001 — тихо оставляем саммари по описанию
            log.warning("youtube deep summary failed for %s: %s", url, str(e)[:200])
            return None


def apply_deep_summary(
    posts: list[RawPost],
    scores: dict[str, dict],
    api_key: str,
    min_score: float,
    max_videos: int,
) -> int:
    """Обогащает summary_ru/key_points для самых релевантных YouTube-видео.

    Мутирует переданный `scores` (temp_id -> dict), возвращает число обновлённых.
    temp_id строится как `item_<index>` — индекс берётся из enumerate(posts).
    """
    if not api_key:
        return 0

    summarizer = YouTubeDeepSummarizer(api_key)
    candidates: list[tuple[RawPost, dict]] = []
    for idx, post in enumerate(posts):
        if post.content_type != "youtube":
            continue
        sc = scores.get(f"item_{idx}")
        if sc and float(sc.get("score") or 0) >= min_score:
            candidates.append((post, sc))

    candidates.sort(key=lambda x: float(x[1].get("score") or 0), reverse=True)

    updated = 0
    for post, sc in candidates[:max_videos]:
        result = summarizer.summarize(post.url)
        if result:
            summary, points = result
            sc["summary_ru"] = summary
            sc["key_points_ru"] = points
            updated += 1
    return updated
