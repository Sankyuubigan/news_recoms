from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from models import BackfillConfig, RawPost, SourceConfig
from parsers.base import BaseParser, ParseError, detect_lang, extract_repo_url, make_title

log = logging.getLogger(__name__)


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


def _extract_external_links(text_el) -> list[str]:
    """Достаёт внешние href из <a href> внутри текста Telegram-поста."""
    if text_el is None:
        return []
    links: list[str] = []
    seen: set[str] = set()
    for a in text_el.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        if re.match(r"^https?://(www\.)?t\.me/", href, re.IGNORECASE):
            continue
        if href not in seen:
            seen.add(href)
            links.append(href)
    return links


def _extract_prev_cursor(soup: BeautifulSoup) -> int | None:
    """Находит id предыдущей страницы в HTML t.me/s/ (link[rel=prev] или data-before)."""
    link = soup.select_one('link[rel="prev"]')
    if link and link.get("href"):
        m = re.search(r"[?&]before=(\d+)", str(link.get("href")))
        if m:
            return int(m.group(1))
    more = soup.select_one(".tme_messages_more[data-before]")
    if more and more.get("data-before"):
        try:
            return int(str(more.get("data-before")))
        except ValueError:
            pass
    return None


def _post_id_int(post: RawPost) -> int | None:
    if not post.external_id:
        return None
    try:
        return int(post.external_id)
    except ValueError:
        return None


class TelegramParser(BaseParser):
    """Парсер публичных каналов через https://t.me/s/<handle> с поддержкой ?before= пагинации."""

    def __init__(self, timeout: float = 15.0, page_delay: float = 0.6) -> None:
        super().__init__(timeout=timeout)
        self.page_delay = page_delay

    def parse(self, source: SourceConfig, limit: int = 25) -> list[RawPost]:
        handle = source.identifier.strip().lstrip("@")
        url = f"https://t.me/s/{handle}"
        html = self.fetch(url)
        return self.parse_html(html, source, limit=limit)

    def parse_page_with_meta(
        self,
        html: str,
        source: SourceConfig,
    ) -> tuple[list[RawPost], int | None, int | None]:
        """Возвращает (посты страницы, min_post_id на странице, prev_cursor из разметки)."""
        soup = BeautifulSoup(html, "html.parser")
        wraps = soup.select(".tgme_widget_message_wrap")
        if not wraps:
            return [], None, None

        prev_cursor = _extract_prev_cursor(soup)
        posts: list[RawPost] = []
        min_id: int | None = None

        for wrap in wraps:
            msg = wrap.select_one(".tgme_widget_message")
            if msg is None:
                continue

            date_el = msg.select_one(".tgme_widget_message_date")
            href = date_el.get("href") if date_el else None
            if not href:
                data_post = msg.get("data-post")
                href = f"https://t.me/{data_post}" if data_post else None
            if not href:
                continue

            post_id = href.rstrip("/").rsplit("/", 1)[-1]
            if post_id.isdigit():
                pid = int(post_id)
                if min_id is None or pid < min_id:
                    min_id = pid

            text_el = msg.select_one(".tgme_widget_message_text")
            text = text_el.get_text("\n", strip=True) if text_el else ""
            if not text:
                continue

            external_links = _extract_external_links(text_el)
            repo_url = extract_repo_url(links=external_links, text=text)

            # Приписываем скрытые в <a href> внешние ссылки, чтобы LLM и фильтры видели URL
            missing_links = [lnk for lnk in external_links if lnk not in text]
            full_text = text
            if missing_links:
                full_text = f"{text}\n\nСсылки: {' '.join(missing_links[:6])}"

            time_el = msg.select_one("time[datetime]")
            published = _iso(time_el.get("datetime") if time_el else None)

            media_url = None
            photo = msg.select_one(".tgme_widget_message_photo_wrap")
            if photo is not None:
                media_url = _media_from_style(photo.get("style"))

            posts.append(
                RawPost(
                    source_identifier=source.identifier,
                    source_name=source.name,
                    kind=source.kind,
                    external_id=post_id,
                    url=href,
                    repo_url=repo_url,
                    content_type="telegram",
                    title_orig=make_title(text),
                    content_orig=full_text[:6000],
                    media_url=media_url,
                    published_at=published,
                    original_lang=detect_lang(text),
                )
            )

        return posts, min_id, prev_cursor

    def parse_html(self, html: str, source: SourceConfig, limit: int = 25) -> list[RawPost]:
        soup = BeautifulSoup(html, "html.parser")
        if not soup.select(".tgme_widget_message_wrap"):
            raise ParseError("no messages found (channel may be private or empty)")
        posts, _, _ = self.parse_page_with_meta(html, source)
        return posts[-limit:] if limit else posts

    def parse_backfill(
        self,
        source: SourceConfig,
        cfg: BackfillConfig,
    ) -> tuple[list[RawPost], BackfillConfig]:
        """Порционно листает историю канала назад через ?before=<id>.

        Возвращает (собранные посты, обновлённый BackfillConfig с новым курсором).
        """
        next_cfg = cfg.model_copy(deep=True)
        if not next_cfg.enabled:
            return [], next_cfg

        handle = source.identifier.strip().lstrip("@")
        target = max(1, min(int(next_cfg.batch_limit or 80), 300))
        if next_cfg.remaining is not None:
            if next_cfg.remaining <= 0:
                next_cfg.enabled = False
                return [], next_cfg
            target = min(target, next_cfg.remaining)

        cursor = next_cfg.cursor_id
        stop_at = next_cfg.stop_at_id
        collected: list[RawPost] = []
        seen_ids: set[int] = set()
        max_pages = max(3, (target // 12) + 4)

        for page_num in range(max_pages):
            if len(collected) >= target:
                break

            url = f"https://t.me/s/{handle}" if cursor is None else f"https://t.me/s/{handle}?before={cursor}"
            try:
                html = self.fetch(url)
            except ParseError as e:
                log.warning("backfill %s stopped on page %d: %s", source.name, page_num + 1, e)
                break

            page_posts, min_id, prev_cursor = self.parse_page_with_meta(html, source)
            if min_id is None:
                next_cfg.enabled = False
                break

            reached_stop = False
            # Идём от более новых к более старым на странице, чтобы курсор
            # всегда указывал точно на границу уже забранного диапазона
            ordered = sorted(page_posts, key=lambda p: _post_id_int(p) or 0, reverse=True)
            last_taken_id: int | None = None

            for post in ordered:
                pid = _post_id_int(post)
                if pid is None:
                    continue
                if stop_at is not None and pid <= stop_at:
                    reached_stop = True
                    break
                if cursor is not None and pid >= cursor:
                    continue
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                collected.append(post)
                last_taken_id = pid
                if len(collected) >= target:
                    break

            if reached_stop:
                next_cfg.cursor_id = stop_at
                next_cfg.enabled = False
                break

            if len(collected) >= target and last_taken_id is not None:
                # Остановились посреди страницы по лимиту порции — следующий запуск продолжит ровно отсюда
                next_cfg.cursor_id = last_taken_id
                break

            next_cursor = prev_cursor if (prev_cursor is not None and prev_cursor <= min_id) else min_id
            if cursor is not None and next_cursor >= cursor:
                next_cfg.enabled = False
                break

            cursor = next_cursor
            next_cfg.cursor_id = cursor

            if stop_at is not None and cursor <= stop_at:
                next_cfg.enabled = False
                break
            if prev_cursor is None:
                # Достигли самого начала канала
                next_cfg.enabled = False
                break
            if self.page_delay > 0:
                time.sleep(self.page_delay)

        next_cfg.processed_total += len(collected)
        if next_cfg.remaining is not None:
            next_cfg.remaining = max(0, next_cfg.remaining - len(collected))
            if next_cfg.remaining == 0:
                next_cfg.enabled = False

        return collected, next_cfg