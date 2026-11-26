-- NewsRecoms — Cloudflare D1 schema (SQLite)
-- Dates: ISO-8601 UTC TEXT

-- 1. Источники контента (Telegram-каналы, RSS сайтов, YouTube-каналы)
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('telegram', 'rss', 'youtube')),
    name TEXT NOT NULL,
    identifier TEXT NOT NULL UNIQUE,
    category_hint TEXT DEFAULT '',
    fetch_full_text INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_fetched_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- 2. Элементы контента (посты, статьи, видео)
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    external_id TEXT,
    url TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    content_type TEXT NOT NULL CHECK(content_type IN ('telegram', 'article', 'youtube')),
    original_lang TEXT DEFAULT 'ru',

    title_orig TEXT NOT NULL,
    content_orig TEXT NOT NULL,
    media_url TEXT,

    title_ru TEXT,
    summary_ru TEXT,
    key_points_json TEXT DEFAULT '[]',
    category TEXT DEFAULT 'Общее',
    tags_json TEXT DEFAULT '[]',
    ai_score REAL,
    ai_reason TEXT,
    is_ad INTEGER NOT NULL DEFAULT 0,
    ad_reason TEXT,

    cluster_slug TEXT,
    merged_sources_json TEXT DEFAULT '[]',

    status TEXT NOT NULL DEFAULT 'pending_ai'
        CHECK(status IN ('pending_ai', 'inbox', 'saved', 'archived', 'rejected')),
    feedback TEXT NOT NULL DEFAULT 'none'
        CHECK(feedback IN ('none', 'like', 'dislike')),
    feedback_reason TEXT,
    feedback_processed INTEGER NOT NULL DEFAULT 0,

    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_items_status_score ON items(status, ai_score DESC, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_status_pub ON items(status, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_content_hash ON items(content_hash);
CREATE INDEX IF NOT EXISTS idx_items_cluster ON items(cluster_slug, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_feedback_pending ON items(feedback, feedback_processed) WHERE feedback != 'none';
CREATE INDEX IF NOT EXISTS idx_items_pending_ai ON items(status) WHERE status = 'pending_ai';

-- 3. Глобальные настройки, ручные правила и самообучающийся профиль
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

INSERT OR IGNORE INTO settings (key, value) VALUES
('manual_rules', 'Интересно: разработка ПО, архитектура, ИИ/LLM, инженерия, качественные технические разборы, новые open-source инструменты, полезные гайды, важные новости индустрии без воды.
Не интересно: кликбейт, инфоцыганство, курсы, прогревы, пустые перепосты без сути, политический спам, криптовалютные сигналы, банальные новости ради трафика.'),
('learned_profile', 'Пока недостаточно реакций пользователя для формирования автоматического профиля. Опирайся на ручные правила.'),
('ad_regex_blacklist', 'erid\s*[:=]\s*[a-zA-Z0-9]+\n#реклама\b\n\bреклама\.\s*\n\bИНН\s*\d{10,12}\b\n\bОГРН\s*\d{13,15}\b\n\bрекламодатель\b\nпромокод\s+[A-ZА-Я0-9]{3,}\nпереходи по ссылке в закреп\nкупить курс\nзапишись на вебинар'),
('min_inbox_score', '6.5'),
('telegram_alert_score', '9.0'),
('learn_feedback_threshold', '10'),
('youtube_deep_summary_enabled', 'true'),
('youtube_deep_summary_min_score', '7.5');

-- 4. Логи запусков пайплайна (наблюдаемость в UI)
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    ad_blocked_count INTEGER NOT NULL DEFAULT 0,
    dedup_skipped_count INTEGER NOT NULL DEFAULT 0,
    ai_processed_count INTEGER NOT NULL DEFAULT 0,
    inbox_added_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    pending_ai_count INTEGER NOT NULL DEFAULT 0,
    provider_stats_json TEXT DEFAULT '{}',
    errors_json TEXT DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running', 'success', 'partial', 'failed'))
);
