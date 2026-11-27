from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

from config import apply_args, get_settings, parse_args
from filters.ad_filter import AdFilter
from filters.dedup import DedupIndex, content_hash, normalize_url
from learner import run_learner
from llm.prompts import REPO_SYSTEM_PROMPT, SYSTEM_PROMPT, build_scoring_user_prompt
from llm.router import (
    AllProvidersFailed,
    ProvidersConfigError,
    build_router_from_json,
    load_providers_raw,
)
from llm.youtube_video import apply_deep_summary
from models import IngestItem, RawPost, RunStats, SourceConfig
from notifier import TelegramNotifier
from parsers.base import ParseError
from parsers.rss import RssParser
from parsers.telegram import TelegramParser
from parsers.youtube import YouTubeParser
from storage import Storage

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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    apply_args(args)
    settings = get_settings()

    try:
        storage = Storage(settings.data_path)
    except OSError as e:
        log.error("cannot open data dir %s: %s", settings.data_path, e)
        return 2

    log.info("data dir: %s", storage.dir)
    storage.merge_user_state()

    stats = RunStats()
    status_ok = True
    llm_unconfigured = False

    bootstrap = storage.bundle()
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
    allow_backfill = len(bootstrap.pending_ai_items) < settings.max_pending_before_backfill_pause
    if not allow_backfill:
        log.info(
            "backfill paused: pending_ai queue has %d items (limit %d)",
            len(bootstrap.pending_ai_items),
            settings.max_pending_before_backfill_pause,
        )

    for source in bootstrap.sources:
        if not source.enabled:
            continue
        parser = parser_for(source, settings.http_timeout_sec)
        try:
            posts = parser.parse(source, limit=settings.max_items_per_source)
            all_posts.extend(posts)
            upd: dict = {
                "identifier": source.identifier,
                "last_fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "last_error": None,
            }

            # Порционный разбор истории канала (Способ 1: t.me/s/?before=ID)
            if (
                allow_backfill
                and isinstance(parser, TelegramParser)
                and source.backfill
                and source.backfill.enabled
            ):
                bf_posts, next_bf = parser.parse_backfill(source, source.backfill)
                all_posts.extend(bf_posts)
                upd["backfill"] = next_bf.model_dump(mode="json")
                log.info(
                    "backfill %-24s +%3d posts (cursor=%s, active=%s)",
                    source.name[:24],
                    len(bf_posts),
                    next_bf.cursor_id,
                    next_bf.enabled,
                )

            source_updates.append(upd)
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

    # ------------------------------------------- 2. layer 1: dedup + ads
    # Сначала дедуп (чтобы уже виденную рекламу не считать повторно каждые 2 часа),
    # затем regex-фильтр с сохранением отсечённого в rejected (видно в «Мусоре ИИ»).
    clean_posts: list[RawPost] = []
    ad_rejected_items: list[IngestItem] = []

    for post in all_posts:
        url = normalize_url(post.url)
        chash = content_hash(post.title_orig, post.content_orig)
        if dedup.is_duplicate(url, chash):
            stats.dedup_skipped_count += 1
            continue
        dedup.add(url, chash)

        reason = ad_filter.check(post)
        if reason:
            stats.ad_blocked_count += 1
            stats.rejected_count += 1
            ad_rejected_items.append(
                _to_ingest(
                    post,
                    {"is_ad": True, "ad_reason": reason, "score": 0.0},
                    status="rejected",
                )
            )
            continue

        clean_posts.append(post)

    log.info(
        "after layer 1: %d clean (%d ads, %d dups)",
        len(clean_posts),
        stats.ad_blocked_count,
        stats.dedup_skipped_count,
    )

    # ------------------------------------------------ 3. layer 2: LLM scoring
    # pending_ai из прошлых запусков идут первыми; ограничиваем объём за один прогон,
    # чтобы крон гарантированно укладывался в таймаут (остаток спокойно уйдёт в pending_ai).
    queued_by_kind: dict[str, list[dict]] = {"news": [], "repos": []}
    ai_budget = max(1, settings.max_ai_items_per_run)
    scheduled_count = 0

    for p in bootstrap.pending_ai_items:
        if scheduled_count >= ai_budget:
            break
        kind = p.get("kind") if p.get("kind") in ("news", "repos") else "news"
        queued_by_kind[kind].append(
            {
                "temp_id": f"pending_{p['id']}",
                "source_name": p.get("source_name") or "—",
                "content_type": p.get("content_type") or "article",
                "title": p.get("title_orig") or "",
                "text": (p.get("content_orig") or "")[:3000],
            }
        )
        scheduled_count += 1

    for i, post in enumerate(clean_posts):
        if scheduled_count >= ai_budget:
            break
        queued_by_kind[post.kind].append(
            {
                "temp_id": f"item_{i}",
                "source_name": post.source_name,
                "content_type": post.content_type,
                "title": post.title_orig,
                "text": post.content_orig[:3000],
            }
        )
        scheduled_count += 1

    total_scheduled = len(queued_by_kind["news"]) + len(queued_by_kind["repos"])
    scores: dict[str, dict] = {}
    router = None

    if total_scheduled:
        # Состояние «ключ выключен» с прошлого прогона (TTL 24ч внутри роутера).
        disabled_snapshot = storage.get_disabled_providers()
        if disabled_snapshot:
            log.info("из прошлого прогона выключено провайдеров: %d", len(disabled_snapshot))

        try:
            router = build_router_from_json(settings.llm_providers_json, disabled=disabled_snapshot)
        except ProvidersConfigError as e:
            log.error("конфигурация LLM невалидна — %d проблем:", len(e.problems))
            for i, p in enumerate(e.problems, 1):
                log.error("  %d. %s", i, p)
                stats.errors.append(f"LLM config: {p}")
            llm_unconfigured = True
        except ValueError as e:
            log.error("LLM is not configured: %s", e)
            stats.errors.append(f"llm not configured: {e}")
            llm_unconfigured = True

        if router is not None:
            stream_configs = (
                ("news", SYSTEM_PROMPT, bundle.manual_rules, bundle.learned_profile),
                ("repos", REPO_SYSTEM_PROMPT, bundle.repo_rules, bundle.repo_learned_profile),
            )
            try:
                for kind, sys_prompt, rules_text, profile_text in stream_configs:
                    payload = queued_by_kind[kind]
                    if not payload:
                        continue
                    clusters = [
                        c for c in bootstrap.recent_clusters if (c.get("kind") or "news") == kind
                    ]
                    results = router.score(
                        sys_prompt,
                        lambda chunk, r=rules_text, p=profile_text, cl=clusters: build_scoring_user_prompt(
                            r, p, chunk, cl
                        ),
                        payload,
                    )
                    for temp_id, res_item in results.items():
                        scores[temp_id] = res_item.model_dump(mode="json")
                    log.info("LLM[%s] scored %d/%d items", kind, len(results), len(payload))
                stats.ai_processed_count = len(scores)
            except AllProvidersFailed as e:
                for temp_id, res_item in e.partial.items():
                    scores[temp_id] = res_item.model_dump(mode="json")
                if scores:
                    log.warning(
                        "LLM остановился (%s), но %d постов из %d уже оценены — сохраняю их",
                        e,
                        len(scores),
                        total_scheduled,
                    )
                log.error("all LLM providers failed: %s", e)
                stats.errors.append(f"all providers failed: {e}")
                stats.ai_processed_count = len(scores)
                status_ok = False
            except Exception as e:  # noqa: BLE001
                log.exception("LLM stage crashed")
                stats.errors.append(f"llm: {e}")
                status_ok = False
            finally:
                stats.provider_stats = router.stats()
                if not settings.dry_run:
                    try:
                        storage.set_disabled_providers(router.disabled_snapshot())
                    except Exception as e:  # noqa: BLE001
                        log.warning("cannot persist disabled providers: %s", e)
    else:
        log.info("nothing to score")

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

    # ----------------------------------- 4. build ingest: ads + pending_ai + new
    ingest_items: list[IngestItem] = list(ad_rejected_items)

    for p in bootstrap.pending_ai_items:
        temp_id = f"pending_{p['id']}"
        sc = scores.get(temp_id)
        if not sc:
            continue  # остаётся pending_ai
        kind = p.get("kind") if p.get("kind") in ("news", "repos") else "news"
        min_score = bundle.min_repos_score if kind == "repos" else bundle.min_inbox_score
        status = _status_for(sc, min_score)
        if status == "rejected":
            stats.rejected_count += 1
        elif status == "inbox":
            stats.inbox_added_count += 1
        ingest_items.append(
            IngestItem(
                source_identifier=p.get("source_identifier")
                or _identifier_by_name(bootstrap.sources, p.get("source_name")),
                kind=kind,
                external_id=p.get("external_id"),
                url=p["url"],
                repo_url=p.get("repo_url") or _valid_http_url(sc.get("repo_url")),
                interface_type=sc.get("interface_type"),
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
                status=status,
                published_at=p.get("published_at")
                or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
        )

    for i, post in enumerate(clean_posts):
        sc = scores.get(f"item_{i}")
        if sc is None:
            # AI не обработал или отложили из-за лимита прогона — в pending_ai
            ingest_items.append(_to_ingest(post, None, status="pending_ai"))
            stats.pending_ai_count += 1
            continue

        min_score = bundle.min_repos_score if post.kind == "repos" else bundle.min_inbox_score
        status = _status_for(sc, min_score)
        if status == "rejected":
            stats.rejected_count += 1
        elif status == "inbox":
            stats.inbox_added_count += 1
        ingest_items.append(_to_ingest(post, sc, status=status))

    # ------------------------------------------------------- 5. write feed
    alerts: list[dict] = []
    if ingest_items and not settings.dry_run:
        try:
            resp = storage.ingest(ingest_items)
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
        log.info("DRY_RUN: would write %d items", len(ingest_items))

    # ------------------------------------------------------- 6. source status
    if source_updates and not settings.dry_run:
        try:
            storage.update_source_status(source_updates)
        except Exception as e:  # noqa: BLE001
            log.warning("source status update failed: %s", e)

    # --------------------------------------------------------- 7. telegram
    notifier = TelegramNotifier(settings.telegram_bot_token, settings.telegram_chat_id)
    if alerts and notifier.enabled:
        sent = notifier.send_alerts(alerts)
        log.info("telegram alerts sent: %d/%d", sent, len(alerts))

    # ---------------------------------------------------------- 8. learner
    if not settings.dry_run and not llm_unconfigured:
        try:
            # Переиспользуем роутер прогона: его состояние уже знает, какие ключи
            # мертвы — learner не будет тратить на них запросы.
            if router is None:
                router = build_router_from_json(
                    settings.llm_providers_json, disabled=storage.get_disabled_providers()
                )
            run_learner(storage, router, bundle)
        except Exception as e:  # noqa: BLE001
            log.warning("learner failed: %s", e)

    # ------------------------------------------------------------- 9. prune
    if not settings.dry_run:
        try:
            storage.prune()
        except Exception as e:  # noqa: BLE001
            log.warning("prune failed: %s", e)

    # ------------------------------------------------------------- 10. stats
    if llm_unconfigured and stats.fetched_count:
        # Данные собраны и записаны — просто без AI-скоринга. Это не фейл:
        # иначе шаг коммита в ветку data не выполнится и новости пропадут.
        stats.status = "partial"
    else:
        stats.status = (
            "success"
            if status_ok and not stats.errors
            else ("partial" if stats.ai_processed_count else "failed")
        )
    if not settings.dry_run:
        try:
            storage.record_run(stats)
            storage.save()
        except Exception as e:  # noqa: BLE001
            log.exception("save failed")
            stats.errors.append(f"save: {e}")
            status_ok = False
    else:
        log.info("DRY_RUN: files not written")

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


def _valid_http_url(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    u = raw.strip()
    return u if u.startswith(("http://", "https://")) else None


def _to_ingest(post: RawPost, sc: dict | None, status: str) -> IngestItem:
    return IngestItem(
        source_identifier=post.source_identifier,
        kind=post.kind,
        external_id=post.external_id,
        url=post.url,
        repo_url=post.repo_url or _valid_http_url((sc or {}).get("repo_url")),
        interface_type=(sc or {}).get("interface_type"),
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
