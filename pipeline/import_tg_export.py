"""Запасной импорт истории Telegram-канала из экспорта Telegram Desktop (JSON).

Использование:
    python import_tg_export.py \
        --export result.json \
        --data-dir ../data_repo \
        --handle my_channel \
        --kind repos \
        --limit 2000

Посты проходят дедуп и Слой 1 (анти-реклама) и попадают в feed.json со статусом
`pending_ai`, после чего регулярный крон сам порциями оценивает их через ИИ.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filters.ad_filter import AdFilter
from filters.dedup import DedupIndex, content_hash, normalize_url
from models import ContentKind, IngestItem, RawPost
from parsers.base import detect_lang, extract_repo_url, make_title
from storage import Storage


def _extract_text_and_links(raw_text: Any) -> tuple[str, list[str]]:
    if isinstance(raw_text, str):
        return raw_text.strip(), []
    if not isinstance(raw_text, list):
        return "", []
    parts: list[str] = []
    links: list[str] = []
    for chunk in raw_text:
        if isinstance(chunk, str):
            parts.append(chunk)
        elif isinstance(chunk, dict):
            text = str(chunk.get("text") or "")
            parts.append(text)
            href = str(chunk.get("href") or "").strip()
            if href.startswith(("http://", "https://")):
                links.append(href)
            elif chunk.get("type") == "link" and text.startswith(("http://", "https://")):
                links.append(text.strip())
    return "".join(parts).strip(), links


def _to_iso(raw_date: str | None) -> str:
    if not raw_date:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def import_export(
    export_path: Path,
    data_dir: Path,
    handle: str,
    kind: ContentKind = "repos",
    source_name: str = "",
    limit: int = 2000,
    min_id: int | None = None,
) -> dict[str, int]:
    data = json.loads(export_path.read_text(encoding="utf-8"))
    messages = data.get("messages") or []
    clean_handle = handle.strip().lstrip("@")
    name = source_name.strip() or str(data.get("name") or clean_handle)

    storage = Storage(data_dir)
    bundle = storage.bundle()
    ad_filter = AdFilter(bundle.bundle().ad_regex_blacklist)
    dedup = DedupIndex(bundle.recent_urls, bundle.recent_hashes)

    selected = [m for m in messages if isinstance(m, dict) and m.get("type") == "message"]
    if limit > 0:
        selected = selected[-limit:]

    ingest_items: list[IngestItem] = []
    stats = {"read": len(selected), "queued": 0, "ads": 0, "dups": 0, "empty": 0}

    for msg in selected:
        msg_id = msg.get("id")
        if min_id is not None and isinstance(msg_id, int) and msg_id <= min_id:
            continue

        text, links = _extract_text_and_links(msg.get("text"))
        if not text:
            stats["empty"] += 1
            continue

        url = f"https://t.me/{clean_handle}/{msg_id}" if msg_id else ""
        if not url:
            continue

        repo_url = extract_repo_url(links=links, text=text)
        extra = [u for u in links if u not in text]
        full_text = f"{text}\n\nСсылки: {' '.join(extra[:6])}" if extra else text

        post = RawPost(
            source_identifier=clean_handle,
            source_name=name,
            kind=kind,
            external_id=str(msg_id),
            url=url,
            repo_url=repo_url,
            content_type="telegram",
            title_orig=make_title(text),
            content_orig=full_text[:6000],
            published_at=_to_iso(msg.get("date")),
            original_lang=detect_lang(text),
        )

        norm = normalize_url(post.url)
        chash = content_hash(post.title_orig, post.content_orig)
        if dedup.is_duplicate(norm, chash):
            stats["dups"] += 1
            continue
        dedup.add(norm, chash)

        ad_reason = ad_filter.check(post)
        if ad_reason:
            stats["ads"] += 1
            ingest_items.append(
                IngestItem(
                    source_identifier=clean_handle,
                    kind=kind,
                    external_id=post.external_id,
                    url=post.url,
                    repo_url=repo_url,
                    content_hash=chash,
                    content_type="telegram",
                    original_lang=post.original_lang,
                    title_orig=post.title_orig,
                    content_orig=post.content_orig,
                    is_ad=True,
                    ad_reason=ad_reason,
                    ai_score=0.0,
                    status="rejected",
                    published_at=post.published_at,
                )
            )
            continue

        stats["queued"] += 1
        ingest_items.append(
            IngestItem(
                source_identifier=clean_handle,
                kind=kind,
                external_id=post.external_id,
                url=post.url,
                repo_url=repo_url,
                content_hash=chash,
                content_type="telegram",
                original_lang=post.original_lang,
                title_orig=post.title_orig,
                content_orig=post.content_orig,
                status="pending_ai",
                published_at=post.published_at,
            )
        )

    if ingest_items:
        storage.ingest(ingest_items)
        storage.save()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Импорт result.json из Telegram Desktop в ветку data")
    parser.add_argument("--export", required=True, help="Путь к файлу result.json")
    parser.add_argument("--data-dir", default="../data_repo", help="Путь к локальному чекауту ветки data")
    parser.add_argument("--handle", required=True, help="Username канала (без @)")
    parser.add_argument("--name", default="", help="Название источника")
    parser.add_argument("--kind", choices=("news", "repos"), default="repos", help="Поток: news или repos")
    parser.add_argument("--limit", type=int, default=2000, help="Сколько последних сообщений импортировать")
    parser.add_argument("--after-id", type=int, default=None, help="Брать только сообщения с id > N")
    args = parser.parse_args(argv)

    stats = import_export(
        export_path=Path(args.export),
        data_dir=Path(args.data_dir),
        handle=args.handle,
        kind=args.kind,
        source_name=args.name,
        limit=args.limit,
        min_id=args.after_id,
    )
    print(
        f"Готово: прочитано={stats['read']} в_очередь_ИИ={stats['queued']} "
        f"реклама={stats['ads']} дубли={stats['dups']} пустые={stats['empty']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())