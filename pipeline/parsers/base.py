from __future__ import annotations

import re
from abc import ABC, abstractmethod
from urllib.parse import urlparse

import httpx

from models import RawPost, SourceConfig

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_REPO_HOSTS = ("github.com", "gitlab.com", "codeberg.org", "huggingface.co")
_REPO_SKIP_OWNERS = {
    "topics",
    "explore",
    "trending",
    "search",
    "settings",
    "notifications",
    "marketplace",
    "sponsors",
    "orgs",
    "features",
    "pricing",
    "about",
    "login",
    "join",
}
_URL_RE = re.compile(r"https?://[^\s<>'\"()\[\]]+")


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
    m = re.match(r"^(.{10,}?[.!?…])(\s|$)", cleaned)
    candidate = m.group(1) if m else cleaned
    if len(candidate) <= limit:
        return candidate
    cut = candidate[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:-—") + "…"


def _canonical_repo_url(raw: str) -> str | None:
    url = (raw or "").strip().rstrip(".,;:!?)]}>")
    if not url.startswith(("http://", "https://")):
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _REPO_HOSTS:
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if owner.lower() in _REPO_SKIP_OWNERS:
        return None
    repo = re.sub(r"\.git$", "", repo)
    if not repo:
        return None
    return f"https://{host}/{owner}/{repo}"


def extract_repo_url(
    primary_url: str = "",
    links: list[str] | None = None,
    text: str = "",
) -> str | None:
    """Находит каноническую ссылку на репозиторий (GitHub/GitLab/Codeberg/HF)."""
    candidates: list[str] = []
    if primary_url:
        candidates.append(primary_url)
    if links:
        candidates.extend(links)
    if text:
        candidates.extend(_URL_RE.findall(text))

    for candidate in candidates:
        canonical = _canonical_repo_url(candidate)
        if canonical:
            return canonical
    return None


def detect_lang(text: str, declared: str = "") -> str:
    """Определяет язык ('ru' | 'en') по заголовку/тексту, если источник не указал явно."""
    low = (declared or "").strip().lower()
    if low.startswith("en"):
        return "en"
    if low.startswith("ru"):
        return "ru"
    sample = (text or "")[:600]
    cyr = len(re.findall(r"[А-Яа-яЁё]", sample))
    lat = len(re.findall(r"[A-Za-z]", sample))
    if lat >= 20 and cyr * 3 < lat:
        return "en"
    return "ru"