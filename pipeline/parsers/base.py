from __future__ import annotations

import re
from abc import ABC, abstractmethod

import httpx

from models import RawPost, SourceConfig

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class ParseError(Exception):
    """Ошибка парсинга конкретного источника (не роняет пайплайн)."""


class BaseParser(ABC):
    def __init__(self, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            },
        )

    def fetch(self, url: str) -> str:
        """GET с одним ретраем на сетевые сбои."""
        last: Exception | None = None
        for _ in range(2):
            try:
                with self._client() as client:
                    res = client.get(url)
                    res.raise_for_status()
                    return res.text
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                last = e
        raise ParseError(f"fetch failed for {url}: {last}")

    def fetch_bytes(self, url: str) -> bytes:
        last: Exception | None = None
        for _ in range(2):
            try:
                with self._client() as client:
                    res = client.get(url)
                    res.raise_for_status()
                    return res.content
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                last = e
        raise ParseError(f"fetch failed for {url}: {last}")

    @abstractmethod
    def parse(self, source: SourceConfig, limit: int = 25) -> list[RawPost]:
        ...


def make_title(text: str, limit: int = 110) -> str:
    """Первая строка/фраза текста как заголовок."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return ""
    # первое предложение
    m = re.match(r"^(.{10,}?[.!?…])(\s|$)", cleaned)
    candidate = m.group(1) if m else cleaned
    if len(candidate) <= limit:
        return candidate
    cut = candidate[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-—") + "…"
