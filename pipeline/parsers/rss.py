from __future__ import annotations

import logging
from datetime import datetime, timezone

import feedparser

from models import RawPost, SourceConfig
from parsers.base import BaseParser, ParseError, detect_lang, extract_repo_url, make_title

log = logging.getLogger(__name__)


def _iso(entry: dict) -> str:
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            try:
                dt = datetime(*parsed[:6], tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            except (TypeError, ValueError):
                continue
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_html(raw: str) -> str:
    from bs4 import BeautifulSoup

    if not raw:
        return ""
    return BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)


def _html_links(raw: str) -> list[str]:
    from bs4 import BeautifulSoup

    if not raw:
        return []
    soup = BeautifulSoup(raw, "html.parser")
    return [str(a.get("href")).strip() for a in soup.select("a[href]") if a.get("href")]


class RssParser(BaseParser):
    """RSS/Atom через feedparser + опциональный trafilatura для полного текста."""

    MAX_CONTENT = 4500

    def parse(self, source: SourceConfig, limit: int = 25) -> list[RawPost]:
        raw = self.fetch_bytes(source.identifier)
        feed = feedparser.parse(raw)
        entries = feed.get("entries") or []
        if not entries:
            raise ParseError("no entries in feed")

        posts: list[RawPost] = []
        for entry in entries[:limit]:
            link = (entry.get("link") or "").strip()
            if not link:
                continue

            raw_summary = entry.get("summary") or entry.get("description") or ""
            title = _clean_html(entry.get("title") or "")
            summary = _clean_html(raw_summary)
            repo_url = extract_repo_url(primary_url=link, links=_html_links(raw_summary), text=summary)

            content = summary
            if source.fetch_full_text or len(summary) < 250:
                full = self._extract_full_text(link)
                if full:
                    content = full

            if not title and not content:
                continue

            media_url = None
            for media in entry.get("media_content") or []:
                if isinstance(media, dict) and media.get("url"):
                    media_url = media["url"]
                    break
            if not media_url:
                for thumb in entry.get("media_thumbnail") or []:
                    if isinstance(thumb, dict) and thumb.get("url"):
                        media_url = thumb["url"]
                        break
            if not media_url:
                for enc in entry.get("enclosures") or []:
                    if isinstance(enc, dict) and str(enc.get("type", "")).startswith("image"):
                        media_url = enc.get("href")
                        break

            declared_lang = feed.get("feed", {}).get("language") or ""
            original_lang = detect_lang(f"{title} {summary}", declared_lang)

            posts.append(
                RawPost(
                    source_identifier=source.identifier,
                    source_name=source.name,
                    kind=source.kind,
                    external_id=entry.get("id") or link,
                    url=link,
                    repo_url=repo_url,
                    content_type="article",
                    title_orig=(title or make_title(content))[:300],
                    content_orig=content[: self.MAX_CONTENT],
                    media_url=media_url,
                    published_at=_iso(entry),
                    original_lang=original_lang,
                )
            )

        return posts

    def _extract_full_text(self, url: str) -> str | None:
        """Мягкое извлечение текста статьи; при неудаче — None (fallback на RSS summary)."""
        try:
            import trafilatura

            raw = self.fetch(url)
            extracted = trafilatura.extract(
                raw,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
            )
            if extracted and len(extracted) > 200:
                return extracted.strip()
        except Exception as e:  # noqa: BLE001 — любой сбой сайта не должен ронять пайплайн
            log.debug("trafilatura failed for %s: %s", url, e)
        return None
