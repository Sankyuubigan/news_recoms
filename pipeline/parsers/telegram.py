from __future__ import annotations

import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from models import RawPost, SourceConfig
from parsers.base import BaseParser, ParseError, make_title


def _iso(dt_str: str | None) -> str:
    if not dt_str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _media_from_style(style: str | None) -> str | None:
    if not style:
        return None
    m = re.search(r"url\(\s*['\"]?(.*?)['\"]?\s*\)", style)
    return m.group(1) if m else None


class TelegramParser(BaseParser):
    """Парсер публичных каналов через https://t.me/s/<handle>."""

    def parse(self, source: SourceConfig, limit: int = 25) -> list[RawPost]:
        handle = source.identifier.strip().lstrip("@")
        url = f"https://t.me/s/{handle}"
        html = self.fetch(url)
        return self.parse_html(html, source, limit=limit)

    def parse_html(self, html: str, source: SourceConfig, limit: int = 25) -> list[RawPost]:
        soup = BeautifulSoup(html, "html.parser")
        wraps = soup.select(".tgme_widget_message_wrap")
        if not wraps:
            raise ParseError("no messages found (channel may be private or empty)")

        posts: list[RawPost] = []
        for wrap in wraps:
            msg = wrap.select_one(".tgme_widget_message")
            if msg is None:
                continue

            text_el = msg.select_one(".tgme_widget_message_text")
            text = text_el.get_text("\n", strip=True) if text_el else ""
            if not text:
                # сервисные сообщения без текста
                continue

            date_el = msg.select_one(".tgme_widget_message_date")
            href = date_el.get("href") if date_el else None
            if not href:
                data_post = msg.get("data-post")
                href = f"https://t.me/{data_post}" if data_post else None
            if not href:
                continue

            time_el = msg.select_one("time[datetime]")
            published = _iso(time_el.get("datetime") if time_el else None)

            media_url = None
            photo = msg.select_one(".tgme_widget_message_photo_wrap")
            if photo is not None:
                media_url = _media_from_style(photo.get("style"))

            post_id = href.rstrip("/").rsplit("/", 1)[-1]

            posts.append(
                RawPost(
                    source_identifier=source.identifier,
                    source_name=source.name,
                    external_id=post_id,
                    url=href,
                    content_type="telegram",
                    title_orig=make_title(text),
                    content_orig=text[:6000],
                    media_url=media_url,
                    published_at=published,
                    original_lang="ru",
                )
            )

        return posts[-limit:] if limit else posts
