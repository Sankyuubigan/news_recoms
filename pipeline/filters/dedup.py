from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "fbclid",
    "gclid",
    "yclid",
    "ref",
    "from",
    "feature",
    "si",
    "s",
    "share",
    "igshid",
    "mc_cid",
    "mc_eid",
    "_ga",
}


def normalize_url(url: str) -> str:
    """Убирает трекинг-параметры, приводит домен/схему к нижнему регистру, срезает trailing slash."""
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip()

    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
    ]
    query = urlencode(sorted(query_pairs))

    path = parsed.path.rstrip("/") or "/"
    if path == "/" and not query:
        path = ""

    return urlunparse((scheme, netloc, path, "", query, ""))


def content_hash(title: str, content: str) -> str:
    """SHA256 от нормализованного заголовка + первых 400 символов текста."""
    def norm(s: str) -> str:
        s = (s or "").lower()
        s = re.sub(r"https?://\S+", " ", s)
        s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
        return re.sub(r"\s+", " ", s).strip()

    basis = f"{norm(title)[:200]}|{norm(content)[:400]}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


class DedupIndex:
    """Индекс уже виденных URL и хэшей (из bootstrap за 14 дней)."""

    def __init__(self, urls: list[str] | None = None, hashes: list[str] | None = None) -> None:
        self._urls = {normalize_url(u) for u in (urls or [])}
        self._hashes = set(hashes or [])

    def is_duplicate(self, url: str, chash: str) -> bool:
        return normalize_url(url) in self._urls or chash in self._hashes

    def add(self, url: str, chash: str) -> None:
        self._urls.add(normalize_url(url))
        self._hashes.add(chash)
