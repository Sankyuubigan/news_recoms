from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from filters.dedup import normalize_url
from models import (
    Bootstrap,
    IngestItem,
    RunStats,
    SettingsBundle,
    SourceConfig,
)

log = logging.getLogger(__name__)

FEED_FILE = "feed.json"
USER_STATE_FILE = "user_state.json"
CONFIG_FILE = "config.json"
RUNS_FILE = "runs.json"

FEED_MAX_ITEMS = 1000
REJECTED_TTL_DAYS = 7
ARCHIVED_TTL_DAYS = 30
DEDUP_LOOKBACK_DAYS = 14
CLUSTER_LOOKBACK_HOURS = 48
RUNS_KEEP = 30

# Чтобы feed.json оставался лёгким, исходный текст храним урезанным.
# Фронтенд content_orig не рендерит (использует summary_ru/key_points), а для
# дообработки pending_ai нужен полный текст (в LLM уходит [:3000]).
# Поэтому оценённым постам оставляем короткий сниппет: это в разы уменьшает
# feed.json (важно — Contents API не отдаёт base64-файлы больше 1 МБ).
CONTENT_KEEP = 300
CONTENT_KEEP_PENDING = 3000

DEFAULT_SETTINGS: dict[str, str] = {
    "manual_rules": (
        "Интересно: разработка ПО, архитектура, ИИ/LLM, инженерия, качественные технические разборы, "
        "новые open-source инструменты, полезные гайды, важные новости индустрии без воды.\n"
        "Не интересно: кликбейт, инфоцыганство, курсы, прогревы, пустые перепосты без сути, "
        "политический спам, криптовалютные сигналы, банальные новости ради трафика."
    ),
    "learned_profile": (
        "Пока недостаточно реакций пользователя для формирования автоматического профиля. "
        "Опирайся на ручные правила."
    ),
    "repo_rules": (
        "Интересно: практичные open-source инструменты, приложения с удобным GUI/Web-интерфейсом, "
        "полезные self-hosted сервисы, расширения, зрелые библиотеки и рабочие AI/dev-инструменты, "
        "которые экономят время и решают реальные задачи.\n"
        "Мусор (ставить 1–3): развлекательные эксперименты и челленджи без практической пользы, "
        "нецелевые порты ПО на экзотическое железо, узкий аппаратный хакинг ради демонстрации, "
        "избыточные терминальные/консольные клоны повседневных программ, учебные переписывания "
        "базовых утилит без реального выигрыша и пустые обёртки."
    ),
    "repo_learned_profile": (
        "Пока недостаточно реакций пользователя по репозиториям. Опирайся на правила для репозиториев."
    ),
    "ad_regex_blacklist": (
        "erid\\s*[:=]\\s*[a-zA-Z0-9]+\n"
        "#реклама\\b\n"
        "\\bреклама\\.\\s*\n"
        "\\bИНН\\s*\\d{10,12}\\b\n"
        "\\bОГРН\\s*\\d{13,15}\\b\n"
        "\\bрекламодатель\\b\n"
        "промокод\\w*\\s*[:=]?\\s*[A-ZА-Я0-9]{3,}\n"
        "переходи по ссылке в закреп\n"
        "купить курс\n"
        "запишись на вебинар"
    ),
    "min_inbox_score": "6.5",
    "min_repos_score": "6.5",
    "telegram_alert_score": "9.0",
    "learn_feedback_threshold": "10",
    "youtube_deep_summary_enabled": "true",
    "youtube_deep_summary_min_score": "7.5",
}

# Стартовые источники: чтобы первый же запуск пайплайна дал непустую ленту.
# Юзер может удалить/добавить любые в Настройках.
DEFAULT_SOURCES: list[dict[str, Any]] = [
    {
        "id": 1,
        "type": "rss",
        "name": "Habr — Новости",
        "identifier": "https://habr.com/ru/rss/news/?fl=ru",
        "category_hint": "Разработка",
        "fetch_full_text": 0,
        "enabled": 1,
    },
    {
        "id": 2,
        "type": "rss",
        "name": "OpenNET",
        "identifier": "https://www.opennet.ru/opennews/opennews_all_utf.rss",
        "category_hint": "Open Source",
        "fetch_full_text": 0,
        "enabled": 1,
    },
    {
        "id": 3,
        "type": "rss",
        "name": "3DNews",
        "identifier": "https://3dnews.ru/news/rss/",
        "category_hint": "Индустрия",
        "fetch_full_text": 0,
        "enabled": 1,
    },
    {
        "id": 4,
        "type": "telegram",
        "kind": "news",
        "name": "AI Newz",
        "identifier": "ai_newz",
        "category_hint": "ИИ",
        "fetch_full_text": 0,
        "enabled": 1,
    },
    {
        "id": 5,
        "type": "rss",
        "kind": "repos",
        "name": "GitHub Trending (Daily)",
        "identifier": "https://mshibanami.github.io/GitHubTrendingRSS/daily/all.xml",
        "category_hint": "Open Source",
        "fetch_full_text": 0,
        "enabled": 1,
    },
    {
        "id": 6,
        "type": "rss",
        "kind": "repos",
        "name": "Show HN (50+)",
        "identifier": "https://hnrss.org/show?points=50",
        "category_hint": "Инструменты",
        "fetch_full_text": 0,
        "enabled": 1,
    },
    {
        "id": 7,
        "type": "rss",
        "kind": "repos",
        "name": "selfh.st",
        "identifier": "https://selfh.st/rss/",
        "category_hint": "Self-hosted",
        "fetch_full_text": 0,
        "enabled": 1,
    },
    {
        "id": 8,
        "type": "rss",
        "kind": "repos",
        "name": "Console.dev",
        "identifier": "https://console.dev/rss.xml",
        "category_hint": "DevTools",
        "fetch_full_text": 0,
        "enabled": 1,
    },
    {
        "id": 9,
        "type": "rss",
        "kind": "repos",
        "name": "Lobsters: Show",
        "identifier": "https://lobste.rs/t/show.rss",
        "category_hint": "Open Source",
        "fetch_full_text": 0,
        "enabled": 1,
    },
    {
        "id": 10,
        "type": "telegram",
        "kind": "repos",
        "name": "Находки в опенсорсе",
        "identifier": "opensource_findings",
        "category_hint": "Open Source",
        "fetch_full_text": 0,
        "enabled": 1,
    },
]

VALID_STATUSES = {"pending_ai", "inbox", "saved", "archived", "rejected"}
VALID_FEEDBACK = {"none", "like", "dislike"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_item_id(url: str) -> str:
    """Стабильный строковый id: первые 16 hex-символов SHA256 от нормализованного URL."""
    import hashlib

    norm = normalize_url(url) or (url or "").strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _atomic_write(path: Path, payload: Any) -> None:
    """Атомарная запись JSON: temp-файл в том же каталоге + os.replace."""
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


class Storage:
    """Файловое хранилище на ветке `data` (Git-as-a-Database).

    Четыре файла:
      feed.json        — новости/статьи/видео (пишет пайплайн)
      user_state.json  — только действия пользователя (пишет фронтенд, читает пайплайн)
      config.json      — источники и настройки (пишут и фронтенд, и пайплайн: learned_profile)
      runs.json        — последние прогоны пайплайна (пишет пайплайн)
    """

    def __init__(self, data_dir: str | Path) -> None:
        self.dir = Path(data_dir).expanduser().resolve()
        self.dir.mkdir(parents=True, exist_ok=True)

        self.config: dict[str, Any] = {}
        self.feed: list[dict[str, Any]] = []
        self.user_state: dict[str, Any] = {}
        self.runs: dict[str, Any] = {}

        self._dirty_config = False
        self._dirty_feed = False
        self._dirty_user_state = False
        self._dirty_runs = False

        self._load()

    # ------------------------------------------------------------- load/init

    def _load(self) -> None:
        self.config = self._load_or_init(
            CONFIG_FILE,
            {
                "updated_at": now_iso(),
                "next_source_id": max((s["id"] for s in DEFAULT_SOURCES), default=0) + 1,
                "sources": [self._normalize_source(s) for s in DEFAULT_SOURCES],
                "settings": dict(DEFAULT_SETTINGS),
            },
        )
        self.feed = self._load_or_init(FEED_FILE, [])
        self.user_state = self._load_or_init(
            USER_STATE_FILE, {"updated_at": now_iso(), "items": {}}
        )
        self.runs = self._load_or_init(RUNS_FILE, {"updated_at": now_iso(), "pipeline_runs": []})

        if not isinstance(self.feed, list):
            log.warning("%s is not a list — resetting to []", FEED_FILE)
            self.feed = []
            self._dirty_feed = True
        if not isinstance(self.user_state.get("items"), dict):
            self.user_state["items"] = {}
            self._dirty_user_state = True
        if not isinstance(self.runs.get("pipeline_runs"), list):
            self.runs["pipeline_runs"] = []
            self._dirty_runs = True

        # Дополняем недостающие настройки дефолтами (после ручного редактирования файла)
        settings = self.config.setdefault("settings", {})
        for key, value in DEFAULT_SETTINGS.items():
            if key not in settings:
                settings[key] = value
                self._dirty_config = True
        self.config.setdefault("sources", [])
        self.config.setdefault(
            "next_source_id", max((s.get("id", 0) for s in self.config["sources"]), default=0) + 1
        )

    def _load_or_init(self, name: str, default: Any) -> Any:
        path = self.dir / name
        if not path.exists():
            log.info("storage: initializing %s", name)
            _atomic_write(path, default)
            return default
        try:
            return _read_json(path)
        except (json.JSONDecodeError, OSError) as e:
            backup = path.with_suffix(path.suffix + ".corrupt")
            log.error("storage: %s is unreadable (%s) — moved to %s, reinitializing", name, e, backup.name)
            try:
                os.replace(path, backup)
            except OSError:
                pass
            _atomic_write(path, default)
            return default

    @staticmethod
    def _normalize_source(raw: dict[str, Any]) -> dict[str, Any]:
        kind = raw.get("kind")
        norm: dict[str, Any] = {
            "id": int(raw.get("id") or 0),
            "type": raw.get("type") or "rss",
            "kind": kind if kind in ("news", "repos") else "news",
            "name": raw.get("name") or raw.get("identifier") or "Источник",
            "identifier": raw.get("identifier") or "",
            "category_hint": raw.get("category_hint") or "",
            "fetch_full_text": int(raw.get("fetch_full_text") or 0),
            "enabled": int(raw.get("enabled", 1)),
            "last_fetched_at": raw.get("last_fetched_at"),
            "last_error": raw.get("last_error"),
            "created_at": raw.get("created_at") or now_iso(),
        }
        if isinstance(raw.get("backfill"), dict):
            norm["backfill"] = raw["backfill"]
        return norm

    # -------------------------------------------------------------- accessors

    @property
    def sources(self) -> list[SourceConfig]:
        out: list[SourceConfig] = []
        for raw in self.config.get("sources", []):
            try:
                out.append(SourceConfig.model_validate(self._normalize_source(raw)))
            except Exception as e:  # noqa: BLE001
                log.warning("storage: bad source config skipped: %s", e)
        return out

    @property
    def settings(self) -> dict[str, str]:
        merged = dict(DEFAULT_SETTINGS)
        merged.update({k: str(v) for k, v in (self.config.get("settings") or {}).items()})
        return merged

    def source_by_identifier(self, identifier: str) -> dict[str, Any] | None:
        for s in self.config.get("sources", []):
            if s.get("identifier") == identifier:
                return s
        return None

    # -------------------------------------------------- disabled LLM providers
    # Ключ, умерший от 401, не должен тратить запрос в каждом следующем прогоне.
    # Храним в config.json → settings.disabled_providers как JSON-СТРОКУ, чтобы не
    # менять тип DataConfig.settings (Record<string,string>) на фронтенде.

    DISABLED_SETTING_KEY = "disabled_providers"

    def get_disabled_providers(self) -> dict[str, dict]:
        """Снимок выключенных ключей: {state_key: {name, reason, at, until, model}}."""
        raw = (self.config.get("settings") or {}).get(self.DISABLED_SETTING_KEY) or ""
        if not raw:
            return {}
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            log.warning("storage: %s is not valid JSON — ignoring", self.DISABLED_SETTING_KEY)
            return {}
        if not isinstance(data, list):
            return {}

        out: dict[str, dict] = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("key") or "").strip()
            if key:
                out[key] = entry
        return out

    def set_disabled_providers(self, entries: list[dict]) -> dict:
        """Перезаписывает снимок целиком (только выключенные, без секретов)."""
        settings = self.config.setdefault("settings", {})
        settings[self.DISABLED_SETTING_KEY] = json.dumps(entries, ensure_ascii=False)
        self.config["updated_at"] = now_iso()
        self._dirty_config = True

        if entries:
            log.warning(
                "storage: %d провайдер(ов) помечены выключенными до следующей перепроверки: %s",
                len(entries),
                ", ".join(f"{e.get('name')} ({e.get('key_fp')})" for e in entries),
            )
            for e in entries:
                log.warning("  • %s: %s (до %s)", e.get("name"), e.get("reason"), e.get("until"))
        else:
            log.info("storage: выключенных провайдеров нет — снимок очищен")
        return {"ok": True, "count": len(entries)}

    # ------------------------------------------------------------ user state

    def merge_user_state(self) -> int:
        """Переносит статусы/фидбек из user_state.json в feed.json.

        Правило: user_state главнее, если его updated_at новее, чем у элемента ленты.
        Возвращает число применённых изменений.
        """
        applied = 0
        by_id = {it.get("id"): it for it in self.feed}
        for item_id, us in (self.user_state.get("items") or {}).items():
            item = by_id.get(item_id)
            if not item:
                continue
            us_ts = _parse_iso(us.get("updated_at"))
            it_ts = _parse_iso(item.get("updated_at"))
            if us_ts and it_ts and us_ts < it_ts:
                continue

            changed = False
            status = us.get("status")
            if status in VALID_STATUSES and item.get("status") != status:
                item["status"] = status
                changed = True
            feedback = us.get("feedback")
            if feedback in VALID_FEEDBACK and item.get("feedback") != feedback:
                item["feedback"] = feedback
                changed = True
            if "feedback_reason" in us and item.get("feedback_reason") != us.get("feedback_reason"):
                item["feedback_reason"] = us.get("feedback_reason")
                changed = True
            if "feedback_processed" in us and bool(item.get("feedback_processed")) != bool(
                us.get("feedback_processed")
            ):
                item["feedback_processed"] = bool(us.get("feedback_processed"))
                changed = True

            if changed:
                item["updated_at"] = us.get("updated_at") or now_iso()
                self._dirty_feed = True
                applied += 1

        # pending_ai из user_state, которых нет в ленте (например, ленту чистили) — игнорируем
        if applied:
            log.info("storage: merged %d user-state changes into feed", applied)
        return applied

    def unprocessed_feedback(self, kind: str | None = None) -> list[dict]:
        """Элементы с 👍/👎, ещё не учтёнными в профиле (можно фильтровать по kind)."""
        out = []
        for it in self.feed:
            if kind is not None and (it.get("kind") or "news") != kind:
                continue
            if it.get("feedback") in ("like", "dislike") and not it.get("feedback_processed"):
                out.append(
                    {
                        "id": it.get("id"),
                        "kind": it.get("kind") or "news",
                        "feedback": it.get("feedback"),
                        "feedback_reason": it.get("feedback_reason"),
                        "title": it.get("title_ru") or it.get("title_orig"),
                        "category": it.get("category"),
                        "tags": it.get("tags") or [],
                        "ai_reason": it.get("ai_reason"),
                    }
                )
        return out

    def update_profile(self, learned_profile: str, item_ids: list[str], kind: str = "news") -> dict:
        setting_key = "repo_learned_profile" if kind == "repos" else "learned_profile"
        self.config.setdefault("settings", {})[setting_key] = learned_profile
        self.config["updated_at"] = now_iso()
        self._dirty_config = True

        ids = set(item_ids)
        marked = 0
        for it in self.feed:
            if it.get("id") in ids and it.get("feedback") in ("like", "dislike"):
                it["feedback_processed"] = True
                marked += 1
        if marked:
            self._dirty_feed = True

        state_items = self.user_state.setdefault("items", {})
        for item_id in ids:
            entry = state_items.get(item_id)
            if entry:
                entry["feedback_processed"] = True
                entry["updated_at"] = now_iso()
        self.user_state["updated_at"] = now_iso()
        self._dirty_user_state = True

        log.info("storage: profile updated, %d reactions marked processed", marked)
        return {"ok": True, "marked": marked}

    # ---------------------------------------------------------------- bundle

    def bundle(self) -> Bootstrap:
        now = datetime.now(timezone.utc)
        dedup_since = now - timedelta(days=DEDUP_LOOKBACK_DAYS)
        cluster_since = now - timedelta(hours=CLUSTER_LOOKBACK_HOURS)

        recent_urls: list[str] = []
        recent_hashes: list[str] = []
        recent_clusters: list[dict] = []
        pending: list[dict] = []

        for it in self.feed:
            fetched = _parse_iso(it.get("fetched_at"))
            if fetched and fetched >= dedup_since:
                if it.get("url"):
                    recent_urls.append(it["url"])
                if it.get("content_hash"):
                    recent_hashes.append(it["content_hash"])

            published = _parse_iso(it.get("published_at")) or fetched
            if (
                it.get("cluster_slug")
                and published
                and published >= cluster_since
                and it.get("status") in ("inbox", "saved", "pending_ai")
            ):
                recent_clusters.append(
                    {
                        "kind": it.get("kind") or "news",
                        "cluster_slug": it["cluster_slug"],
                        "title_ru": it.get("title_ru") or it.get("title_orig"),
                        "url": it.get("url"),
                    }
                )

            if it.get("status") == "pending_ai":
                pending.append(dict(it))

        return Bootstrap(
            sources=self.sources,
            settings=self.settings,
            recent_urls=recent_urls,
            recent_hashes=recent_hashes,
            recent_clusters=recent_clusters,
            pending_ai_items=pending,
        )

    # ---------------------------------------------------------------- ingest

    def ingest(self, items: list[IngestItem]) -> dict:
        """Складывает результаты прогона в feed.json.

        - дубли по нормализованному URL / content_hash — пропускаются;
        - элементы со статусом pending_ai обновляются на месте;
        - совпадение по cluster_slug за 48ч — схлопывание в merged_sources.
        """
        inserted = updated = merged = 0
        skipped: list[dict] = []
        alerts: list[dict] = []

        by_url: dict[str, dict] = {}
        by_hash: dict[str, dict] = {}
        for it in self.feed:
            u = normalize_url(it.get("url") or "")
            if u:
                by_url.setdefault(u, it)
            h = it.get("content_hash")
            if h:
                by_hash.setdefault(h, it)

        cluster_index: dict[str, dict] = {}
        cluster_since = datetime.now(timezone.utc) - timedelta(hours=CLUSTER_LOOKBACK_HOURS)
        for it in self.feed:
            slug = it.get("cluster_slug")
            if not slug:
                continue
            ts = _parse_iso(it.get("published_at")) or _parse_iso(it.get("fetched_at"))
            if ts and ts >= cluster_since and it.get("status") != "rejected":
                cluster_index.setdefault(slug, it)

        alert_threshold = self._float_setting("telegram_alert_score", 9.0)
        now = now_iso()

        for item in items:
            norm = normalize_url(item.url)
            existing = by_url.get(norm) or by_hash.get(item.content_hash)

            if existing is not None:
                if existing.get("status") == "pending_ai" and item.status != "pending_ai":
                    self._apply_scored_fields(existing, item, now)
                    updated += 1
                else:
                    skipped.append({"url": item.url, "reason": "duplicate"})
                continue

            if item.cluster_slug:
                twin = cluster_index.get(item.cluster_slug)
                if twin is not None:
                    self._add_merged_source(twin, item, now)
                    merged += 1
                    continue

            row = self._build_row(item, now)
            self.feed.append(row)
            inserted += 1
            by_url[norm] = row
            by_hash[item.content_hash] = row
            if item.cluster_slug:
                cluster_index[item.cluster_slug] = row

            score = item.ai_score
            if score is not None and float(score) >= alert_threshold and item.status == "inbox":
                alerts.append(
                    {
                        "id": row["id"],
                        "title_ru": row.get("title_ru"),
                        "summary_ru": row.get("summary_ru"),
                        "ai_score": score,
                        "url": row["url"],
                    }
                )

        if inserted or updated or merged:
            self._dirty_feed = True
            self.feed.sort(key=lambda r: r.get("published_at") or "", reverse=True)

        log.info(
            "storage.ingest: inserted=%d updated=%d merged=%d skipped=%d",
            inserted,
            updated,
            merged,
            len(skipped),
        )
        return {
            "ok": True,
            "inserted": inserted,
            "updated": updated,
            "merged": merged,
            "skipped": skipped,
            "alerts": alerts,
        }

    def _build_row(self, item: IngestItem, now: str) -> dict[str, Any]:
        src = self.source_by_identifier(item.source_identifier) or {}
        keep = CONTENT_KEEP_PENDING if item.status == "pending_ai" else CONTENT_KEEP
        return {
            "id": make_item_id(item.url),
            "kind": item.kind or src.get("kind") or "news",
            "source_identifier": item.source_identifier,
            "source_name": src.get("name") or item.source_identifier or "Источник",
            "source_type": src.get("type") or _type_for_content(item.content_type),
            "external_id": item.external_id,
            "url": item.url,
            "repo_url": item.repo_url,
            "interface_type": item.interface_type,
            "content_hash": item.content_hash,
            "content_type": item.content_type,
            "original_lang": item.original_lang,
            "title_orig": item.title_orig,
            "content_orig": (item.content_orig or "")[:keep],
            "media_url": item.media_url,
            "title_ru": item.title_ru,
            "summary_ru": item.summary_ru,
            "key_points": list(item.key_points),
            "category": item.category or "Общее",
            "tags": list(item.tags),
            "ai_score": item.ai_score,
            "ai_reason": item.ai_reason,
            "is_ad": bool(item.is_ad),
            "ad_reason": item.ad_reason,
            "cluster_slug": item.cluster_slug,
            "merged_sources": list(item.merged_sources),
            "status": item.status,
            "feedback": "none",
            "feedback_reason": None,
            "feedback_processed": False,
            "published_at": item.published_at,
            "fetched_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _apply_scored_fields(row: dict[str, Any], item: IngestItem, now: str) -> None:
        for field in (
            "title_ru",
            "summary_ru",
            "key_points",
            "category",
            "tags",
            "ai_score",
            "ai_reason",
            "is_ad",
            "ad_reason",
            "cluster_slug",
            "media_url",
            "repo_url",
            "interface_type",
        ):
            value = getattr(item, field)
            if value is not None:
                row[field] = list(value) if isinstance(value, list) else value
        row["status"] = item.status
        row["updated_at"] = now

    def _add_merged_source(self, row: dict[str, Any], item: IngestItem, now: str) -> None:
        src = self.source_by_identifier(item.source_identifier) or {}
        entry = {"name": src.get("name") or item.source_identifier or "Источник", "url": item.url}
        merged = row.setdefault("merged_sources", [])
        if not any(m.get("url") == entry["url"] for m in merged):
            merged.append(entry)
        row["updated_at"] = now

    # --------------------------------------------------------- source status

    def update_source_status(self, updates: list[dict[str, Any]]) -> dict:
        touched = 0
        by_ident = {s.get("identifier"): s for s in self.config.get("sources", [])}
        for upd in updates:
            src = by_ident.get(upd.get("identifier"))
            if not src:
                continue
            if "last_fetched_at" in upd:
                src["last_fetched_at"] = upd["last_fetched_at"]
            if "last_error" in upd:
                src["last_error"] = upd["last_error"]
            if "backfill" in upd:
                src["backfill"] = upd["backfill"]
            touched += 1
        if touched:
            self.config["updated_at"] = now_iso()
            self._dirty_config = True
        return {"ok": True, "updated": touched}

    # ------------------------------------------------------------------ runs

    def record_run(self, stats: RunStats) -> dict:
        runs = self.runs.setdefault("pipeline_runs", [])
        run_id = max((int(r.get("id") or 0) for r in runs), default=0) + 1
        runs.append(
            {
                "id": run_id,
                "started_at": stats.started_at,
                "finished_at": now_iso(),
                "fetched_count": stats.fetched_count,
                "ad_blocked_count": stats.ad_blocked_count,
                "dedup_skipped_count": stats.dedup_skipped_count,
                "ai_processed_count": stats.ai_processed_count,
                "inbox_added_count": stats.inbox_added_count,
                "rejected_count": stats.rejected_count,
                "pending_ai_count": stats.pending_ai_count,
                "provider_stats": stats.provider_stats,
                "errors": stats.errors,
                "status": stats.status,
            }
        )
        # Храним только последние RUNS_KEEP прогонов
        if len(runs) > RUNS_KEEP:
            del runs[:-RUNS_KEEP]
        self.runs["pipeline_runs"] = runs
        self.runs["updated_at"] = now_iso()
        self._dirty_runs = True
        return {"ok": True, "id": run_id}

    # ----------------------------------------------------------------- prune

    def prune(self) -> dict:
        """Чистит старый мусор по fetched_at с раздельными лимитами для news и repos.

        Используем fetched_at (а не published_at), чтобы исторические посты из
        разбора архива не удалялись в момент загрузки; `saved` не удаляются никогда.
        """
        now = datetime.now(timezone.utc)
        rejected_cutoff = now - timedelta(days=REJECTED_TTL_DAYS)
        archived_cutoff = now - timedelta(days=ARCHIVED_TTL_DAYS)

        removed = {"rejected": 0, "archived": 0, "overflow": 0}
        by_kind: dict[str, list[dict[str, Any]]] = {}

        for it in self.feed:
            status = it.get("status")
            ts = _parse_iso(it.get("fetched_at")) or _parse_iso(it.get("published_at"))
            kind = it.get("kind") or "news"

            if status == "saved":
                by_kind.setdefault(kind, []).append(it)
                continue
            if status == "rejected" and ts and ts < rejected_cutoff:
                removed["rejected"] += 1
                continue
            if status == "archived" and ts and ts < archived_cutoff and len(self.feed) > FEED_MAX_ITEMS:
                removed["archived"] += 1
                continue
            by_kind.setdefault(kind, []).append(it)

        # Лимит FEED_MAX_ITEMS применяется отдельно на каждый поток (news / repos),
        # чтобы поток новостей не вытеснял отобранные репозитории.
        kept: list[dict[str, Any]] = []
        for items_of_kind in by_kind.values():
            if len(items_of_kind) <= FEED_MAX_ITEMS:
                kept.extend(items_of_kind)
                continue
            saved = [it for it in items_of_kind if it.get("status") == "saved"]
            others = [it for it in items_of_kind if it.get("status") != "saved"]
            others.sort(key=lambda r: r.get("fetched_at") or r.get("published_at") or "", reverse=True)
            allowed = max(FEED_MAX_ITEMS - len(saved), 0)
            removed["overflow"] += max(len(others) - allowed, 0)
            kept.extend(saved + others[:allowed])

        total_removed = sum(removed.values())
        if total_removed:
            self.feed = kept
            self._dirty_feed = True
            log.info(
                "storage.prune: removed rejected=%d archived=%d overflow=%d, %d items left",
                removed["rejected"],
                removed["archived"],
                removed["overflow"],
                len(self.feed),
            )
        return removed

    # ------------------------------------------------------------------ save

    def save(self) -> None:
        if self._dirty_config:
            self.config["updated_at"] = now_iso()
            _atomic_write(self.dir / CONFIG_FILE, self.config)
            self._dirty_config = False
        if self._dirty_feed:
            _atomic_write(self.dir / FEED_FILE, self.feed)
            self._dirty_feed = False
        if self._dirty_user_state:
            self.user_state["updated_at"] = now_iso()
            _atomic_write(self.dir / USER_STATE_FILE, self.user_state)
            self._dirty_user_state = False
        if self._dirty_runs:
            _atomic_write(self.dir / RUNS_FILE, self.runs)
            self._dirty_runs = False

    # ----------------------------------------------------------------- utils

    def _float_setting(self, key: str, default: float) -> float:
        try:
            return float(self.settings.get(key, default))
        except (TypeError, ValueError):
            return default


def _type_for_content(content_type: str) -> str:
    if content_type == "telegram":
        return "telegram"
    if content_type == "youtube":
        return "youtube"
    return "rss"


def bundle_from_storage(storage: Storage) -> SettingsBundle:
    return storage.bundle().bundle()
