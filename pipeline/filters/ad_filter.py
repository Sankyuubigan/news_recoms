from __future__ import annotations

import re

from models import RawPost

# Жёсткие маркеры рекламы, работающие даже без настройки (РУ-Телеграм)
HARD_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"erid\s*[:=]\s*[a-zA-Z0-9]{4,}", "erid"),
    (r"[?&]erid=", "erid_url"),
    (r"#реклама\b", "#реклама"),
    (r"\bреклама\.\s", "Реклама."),
    (r"\bИНН\s*\d{10,12}\b", "ИНН"),
    (r"\bОГРН\s*\d{13,15}\b", "ОГРН"),
    (r"\bрекламодатель\b", "рекламодатель"),
    (r"промокод\w*\s*[:=]?\s*[A-ZА-Я0-9]{3,}", "промокод"),
    (r"переход(и|ите) по ссылке в закреп", "ссылка в закреп"),
    (r"\bкупи(ть)? курс\b", "купить курс"),
    (r"запиши(сь|тесь) на вебинар", "вебинар"),
)


class AdFilter:
    """Слой 1: режет рекламу кодом, до отправки в LLM."""

    def __init__(self, extra_patterns: str = "", extra_blacklist: list[str] | None = None) -> None:
        self._compiled: list[tuple[re.Pattern[str], str]] = []
        for pattern, label in HARD_PATTERNS:
            self._compiled.append((re.compile(pattern, re.IGNORECASE | re.UNICODE), label))

        self._bad_regex: list[str] = []
        for line in (extra_patterns or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                self._compiled.append((re.compile(line, re.IGNORECASE | re.UNICODE), line))
            except re.error:
                self._bad_regex.append(line)

        self._phrases = [p.strip().lower() for p in (extra_blacklist or []) if p.strip()]

    @property
    def invalid_patterns(self) -> list[str]:
        return list(self._bad_regex)

    def check(self, post: RawPost) -> str | None:
        """Возвращает причину блокировки или None, если пост чистый."""
        haystack = f"{post.title_orig}\n{post.content_orig}\n{post.url}"

        for pattern, label in self._compiled:
            m = pattern.search(haystack)
            if m:
                matched = m.group(0).strip()[:60]
                return f"Regex: {label} → «{matched}»"

        lowered = haystack.lower()
        for phrase in self._phrases:
            if phrase in lowered:
                return f"Blacklist: «{phrase}»"

        return None

    def filter(self, posts: list[RawPost]) -> tuple[list[RawPost], list[tuple[RawPost, str]]]:
        clean: list[RawPost] = []
        blocked: list[tuple[RawPost, str]] = []
        for p in posts:
            reason = self.check(p)
            if reason:
                blocked.append((p, reason))
            else:
                clean.append(p)
        return clean, blocked