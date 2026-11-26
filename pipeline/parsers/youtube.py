from __future__ import annotations

import re
from datetime import datetime, timezone

import feedparser

from models import RawPost, SourceConfig
from parsers.base import BaseParser, ParseError


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


class YouTubeParser(BaseParser):
    """YouTube RSS: https://www.youtube.com/feeds/videos.xml?channel_id=<UC...>"""

    def parse(self, source: SourceConfig, limit: int = 25) -> list[RawPost]:
        ident = source.identifier.strip()
        if ident.startswith("@"):
            channel_id = self._resolve_handle(ident)
            if not channel_id:
                raise ParseError(f"cannot resolve channel_id for {ident}")
        elif ident.startswith("UC"):
            channel_id = ident
        else:
            m = re.search(r"channel_id=([A-Za-z0-9_-]+)", ident)
            channel_id = m.group(1) if m else ident

        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        raw = self.fetch_bytes(url)
        feed = feedparser.parse(raw)
        entries = feed.get("entries") or []
        if not entries:
            raise ParseError("no videos in feed")

        posts: list[RawPost] = []
        for entry in entries[:limit]:
            link = (entry.get("link") or "").strip()
            if not link:
                continue
            if "/shorts/" in link:
                continue

            video_id = entry.get("yt_videoid") or (link.rsplit("=", 1)[-1] if "v=" in link else "")
            title = (entry.get("title") or "").strip()
            description = (entry.get("media_description") or entry.get("summary") or "").strip()

            posts.append(
                RawPost(
                    source_identifier=source.identifier,
                    source_name=source.name,
                    external_id=video_id or None,
                    url=link,
                    content_type="youtube",
                    title_orig=title[:300],
                    content_orig=description[:4500],
                    media_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else None,
                    published_at=_iso(entry),
                    original_lang="ru",
                )
            )

        return posts

    def _resolve_handle(self, handle: str) -> str | None:
        """Резолв @handle -> channel_id через HTML страницы канала."""
        try:
            html = self.fetch(f"https://www.youtube.com/{handle}")
        except Exception:  # noqa: BLE001
            return None
        for pattern in (
            r'"channelId":"(UC[A-Za-z0-9_-]{20,})"',
            r'channel_id=(UC[A-Za-z0-9_-]{20,})',
            r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]+)"',
        ):
            m = re.search(pattern, html)
            if m:
                return m.group(1)
        return None
