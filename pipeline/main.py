from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from api_client import WorkerClient
from config import get_settings
from filters.ad_filter import AdFilter
from filters.dedup import DedupIndex, content_hash, normalize_url
from learner import run_learner
from llm.prompts import SYSTEM_PROMPT, build_scoring_user_prompt
from llm.router import AllProvidersFailed, build_router_from_json
from llm.youtube_video import apply_deep_summary
from models import IngestItem, RawPost, RunStats, SourceConfig
from notifier import TelegramNotifier
from parsers.base import ParseError
from parsers.rss import RssParser
from parsers.telegram import TelegramParser
from parsers.youtube import YouTubeParser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("pipeline")


def parser_for(source: SourceConfig, timeout: float):
    if source.type == "telegram":
        return TelegramParser(timeout=timeout)
    if source.type == "youtube":
        return YouTubeParser(timeout=timeout)
    return RssParser(timeout=timeout)


def main() -> int:
    settings = get_settings()
    if not settings.api_base or not settings.ingest_secret:
        log.error("WORKER_BASE_URL and INGEST_SECRET are required")
        return 2

    client = WorkerClient(settings.api_base, settings.ingest_secret)
    stats = RunStats()
    status_ok = True

    try:
        bootstrap = client.bootstrap()
    except Exception as e:  # noqa: BLE001
        log.error("bootstrap failed: %s", e)
        return 3

    bundle = bootstrap.bundle()
    log.info(
        "bootstrap: %d sources, %d recent urls, %d pending_ai",
        len(bootstrap.sources),
        len(bootstrap.recent_urls),
        len(bootstrap.pending_ai_items),
    )

    ad_filter = AdFilter(bundle.ad_regex_blacklist)
    if ad_filter.invalid_patterns:
        log.warning("invalid regex patterns skipped: %s", ad_filter.invalid_patterns)

    dedup = DedupIndex(bootstrap.recent_urls, bootstrap.recent_hashes)

    # ------------------------------------------------------------ 1. collect
    all_posts: list[RawPost] = []
    source_updates: list[dict] = []

    for source in bootstrap.sources:
        if not source.enabled:
            continue
        parser = parser_for(source, settings.http_timeout_sec)
        try:
            posts = parser.parse(source, limit=settings.max_items_per_source)
            all_posts.extend(posts)
            source_updates.append(
                {
                    "identifier": source.identifier,
                    "last_fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "last_error": None,
                }
            )
            log.info("parsed %-28s %3d posts", source.name[:28], len(posts))
        except ParseError as e:
            source_updates.append({"identifier": source.identifier, "last_error": str(e)[:400]})
            stats.errors.append(f"{source.name}: {e}")
            log.warning("source %s failed: %s", source.name, e)
        except Exception as e:  # noqa: BLE001
            source_updates.append({"identifier": source.identifier, "last_error": str(e)[:400]})
            stats.errors.append(f"{source.name}: {e}")
            log.exception("source %s crashed", source.name)

    stats.fetched_count = len(all_posts)
    log.info("collected %d raw posts", len(all_posts))

    # ------------------------------------------- 2. layer 1: ads + dedup
    clean_posts: list[RawPost] = []
    for post in all_posts:
        reason = ad_filter.check(post)
        if reason:
            stats.ad_blocked_count += 1
            continue

        url = normalize_url(post.url)
        chash = content_hash(post.title_orig, post.content_orig)
        if dedup.is_duplicate(url, chash):
            stats.dedup_skipped_count += 1
            continue
        dedup.add(url, chash)
        clean_posts.append(post)

    log.info(
        "after layer 1: %d clean (%d ads, %d dups)",
        len(clean_posts),
        stats.ad_blocked_count,
        stats.dedup_skipped_count,
    )

    # ------------------------------------------------ 3. layer 2: LLM scoring
    ingest_items: list[IngestItem] = []

    # pending_ai из прошлых запусков добавляем первыми
    pending_payload: list[dict] = []
    for p in bootstrap.pending_ai_items:
        pending_payload.append(
            {
                "temp_id": f"pending_{p['id']}",
                "source_name": p.get("source_name") or "—",
                "content_type": p.get("content_type") or "article",
                "title": p.get("title_orig") or "",
                "text": (p.get("content_orig") or "")[:3000],
            }
        )

    new_payload: list[dict] = []
    for i, post in enumerate(clean_posts):
        new_payload.append(
            {
                "temp_id": f"item_{i}",
                "source_name": post.source_name,
                "content_type": post.content_type,
                "title": post.title_orig,
                "text": post.content_orig[:3000],
            }
        )

    all_payload = pending_payload + new_payload
    scores: dict[str, dict] = {}

    if all_payload:
        try:
            router = build_router_from_json(settings.llm_providers_json)
            results = router.score(
                SYSTEM_PROMPT,
                lambda chunk: build_scoring_user_prompt(
                    bundle.manual_rules,
                    bundle.learned_profile,
                    chunk,
                    bootstrap.recent_clusters,
                ),
                all_payload,
            )
            for temp_id, r in results.items():
                scores[temp_id] = r.model_dump(mode="json")
            stats.ai_processed_count = len(scores)
            stats.provider_stats = router.stats()
            log.info("LLM scored %d/%d items", len(scores), len(all_payload))
        except AllProvidersFailed as e:
            log.error("all LLM providers failed: %s", e)
            stats.errors.append(f"all providers failed: {e}")
            status_ok = False
        except Exception as e:  # noqa: BLE001
            log.exception("LLM stage crashed")
            stats.errors.append(f"llm: {e}")
            status_ok = False
    else:
        log.info("nothing to score")

    # ----------------------------------- 4. build ingest: pending_ai + new
    pending_by_id = {f"pending_{p['id']}": p for p in bootstrap.pending_ai_items}

    for temp_id, p in pending_by_id.items():
        sc = scores.get(temp_id)
        if not sc:
            continue  # остаётся pending_ai в БД
        ingest_items.append(
            IngestItem(
                source_identifier=p.get("source_identifier") or _identifier_by_name(bootstrap.sources, p.get("source_name")),
                external_id=p.get("external_id"),
                url=p["url"],
                content_hash=p["content_hash"],
                content_type=p.get("content_type") or "article",
                original_lang=p.get("original_lang") or "ru",
                title_orig=p.get("title_orig") or "",
                content_orig=p.get("content_orig") or "",
                media_url=p.get("media_url"),
                title_ru=sc.get("title_ru"),
                summary_ru=sc.get("summary_ru"),
                key_points=sc.get("key_points_ru") or [],
                category=sc.get("category") or "Общее",
                tags=sc.get("tags") or [],
                ai_score=sc.get("score"),
                ai_reason=sc.get("ai_reason"),
                is_ad=bool(sc.get("is_ad")),
                ad_reason=sc.get("ad_reason"),
                cluster_slug=sc.get("cluster_slug"),
                status=_status_for(sc, bundle.min_inbox_score),
                published_at=p.get("published_at") or datetime.now(timezone.utc).isoformat(),
            )
        )

    # YouTube deep summary для новых видео с высоким score
    if bundle.youtube_deep_summary_enabled and settings.gemini_video_api_key and clean_posts:
        try:
            updated = apply_deep_summary(
                clean_posts,
                scores,
                settings.gemini_video_api_key,
                bundle.youtube_deep_summary_min_score,
                settings.youtube_max_deep_per_run,
            )
            if updated:
                log.info("youtube deep summary applied to %d videos", updated)
        except Exception as e:  # noqa: BLE001
            log.warning("youtube deep summary stage failed: %s", e)

    for i, post in enumerate(clean_posts):
        temp_id = f"item_{i}"
        sc = scores.get(temp_id)
        if sc is None:
            # AI не обработал — в pending_ai, чтобы не потерять
            ingest_items.append(_to_ingest(post, None, bundle.min_inbox_score, status="pending_ai"))
            stats.pending_ai_count += 1
            continue

        status = _status_for(sc, bundle.min_inbox_score)
        if status == "rejected":
            stats.rejected_count += 1
        elif status == "inbox":
            stats.inbox_added_count += 1
        ingest_items.append(_to_ingest(post, sc, bundle.min_inbox_score, status=status))

    # ------------------------------------------------------- 5. send to DB
    alerts: list[dict] = []
    if ingest_items and not settings.dry_run:
        try:
            resp = client.ingest(ingest_items)
            alerts = resp.get("alerts") or []
            log.info(
                "ingest: inserted=%s updated=%s merged=%s skipped=%s",
                resp.get("inserted"),
                resp.get("updated"),
                resp.get("merged"),
                len(resp.get("skipped") or []),
            )
        except Exception as e:  # noqa: BLE001
            log.exception("ingest failed")
            stats.errors.append(f"ingest: {e}")
            status_ok = False
    elif settings.dry_run:
        log.info("DRY_RUN: would send %d items", len(ingest_items))

    # ------------------------------------------------------- 6. source status
    if source_updates and not settings.dry_run:
        try:
            client.update_source_status(source_updates)
        except Exception as e:  # noqa: BLE001
            log.warning("source status update failed: %s", e)

    # --------------------------------------------------------- 7. telegram
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    if alerts and notifier.enabled:
        sent = notifier.send_alerts(alerts)
        log.info("telegram alerts sent: %d/%d", sent, len(alerts))

    # ---------------------------------------------------------- 8. learner
    if not settings.dry_run:
        try:
            router = build_router_from_json(settings.llm_providers_json)
            run_learner(client, router, bundle)
        except Exception as e:  # noqa: BLE001
            log.warning("learner failed: %s", e)

    # ------------------------------------------------------------- 9. stats
    stats.status = "success" if status_ok and not stats.errors else ("partial" if stats.ai_processed_count else "failed")
    if not settings.dry_run:
        try:
            client.record_run(stats)
        except Exception as e:  # noqa: BLE001
            log.warning("record_run failed: %s", e)

    log.info(
        "done: fetched=%d ads=%d dups=%d ai=%d inbox=%d rejected=%d pending=%d status=%s",
        stats.fetched_count,
        stats.ad_blocked_count,
        stats.dedup_skipped_count,
        stats.ai_processed_count,
        stats.inbox_added_count,
        stats.rejected_count,
        stats.pending_ai_count,
        stats.status,
    )
    return 0 if stats.status != "failed" else 1


def _status_for(sc: dict, min_score: float) -> str:
    if sc.get("is_ad"):
        return "rejected"
    score = float(sc.get("score") or 0)
    return "inbox" if score >= min_score else "rejected"


def _identifier_by_name(sources: list[SourceConfig], name: str | None) -> str:
    for s in sources:
        if s.name == name:
            return s.identifier
    return ""


def _to_ingest(post: RawPost, sc: dict | None, min_score: float, status: str) -> IngestItem:
    return IngestItem(
        source_identifier=post.source_identifier,
        external_id=post.external_id,
        url=post.url,
        content_hash=content_hash(post.title_orig, post.content_orig),
        content_type=post.content_type,
        original_lang=post.original_lang,
        title_orig=post.title_orig,
        content_orig=post.content_orig,
        media_url=post.media_url,
        title_ru=(sc or {}).get("title_ru"),
        summary_ru=(sc or {}).get("summary_ru"),
        key_points=(sc or {}).get("key_points_ru") or [],
        category=(sc or {}).get("category") or "Общее",
        tags=(sc or {}).get("tags") or [],
        ai_score=(sc or {}).get("score"),
        ai_reason=(sc or {}).get("ai_reason"),
        is_ad=bool((sc or {}).get("is_ad")),
        ad_reason=(sc or {}).get("ad_reason"),
        cluster_slug=(sc or {}).get("cluster_slug"),
        status=status,
        published_at=post.published_at,
    )


if __name__ == "__main__":
    sys.exit(main())
