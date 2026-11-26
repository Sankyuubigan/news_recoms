# NewsRecoms

Персональный AI-агрегатор новостей: Telegram-каналы, RSS/сайты и YouTube → AI-фильтр мусора →
чистая лента в PWA на телефоне и ПК.

**Всё живёт на GitHub и стоит $0.** Ни Cloudflare, ни серверов, ни карт — только репозиторий:
GitHub Pages отдаёт сайт, GitHub Actions собирает новости по расписанию, а ветка `data` работает
базой данных.

- 🔥 **AI-скоринг 1–10** под твои правила + перевод + саммари + тезисы
- 🚫 **Двухслойная резка рекламы**: regex до нейронки (erid, #реклама, ИНН) + детект нативной рекламы в LLM
- 🧩 **Схлопывание сюжетов**: одна новость из 10 каналов → одна карточка со ссылками на все источники
- 🧠 **Самообучение**: 👍/👎 → LLM переписывает профиль твоих интересов
- 📥 **Inbox Zero**: Новое / Отложено / Архив / Мусор ИИ
- 🔁 **Smart Failover**: Gemini → Groq → OpenRouter, с авто-дроблением батча
- 📱 **Без пароля**: читаешь сразу; синхронизация подключается один раз токеном и QR-кодом

## Архитектура

```text
┌──────────────────── GitHub ────────────────────┐
│                                                │
│  ветка main                     ветка data     │
│  ├─ app/     (React PWA)        ├─ feed.json        ← пишет пайплайн
│  ├─ pipeline/(Python)           ├─ user_state.json  ← пишет фронтенд
│  └─ .github/workflows/          ├─ config.json      ← оба
│                                 └─ runs.json        ← пишет пайплайн
│                                                │
│  Actions: ingest.yml (cron 2ч) → пушит в data  │
│  Actions: deploy-pages.yml     → деплой на Pages│
└────────────────────────────────────────────────┘
        │                                │
        ▼                                ▼
  https://<ник>.github.io/<репо>/   GitHub REST API (чтение/запись JSON)
```

**Почему разделены файлы:** пайплайн пишет только `feed.json`, `runs.json` и `config.json→learned_profile`,
фронтенд — только `user_state.json` (и `config.json`, когда меняешь источники/правила).
Конфликтов записи практически нет, а если случится (409) — клиент перечитывает файл,
мержит по `updated_at` и повторяет.

## Быстрый старт — 5 шагов

### Шаг 1. Создай **публичный** репозиторий и запушь код

```bash
git clone <этот-репозиторий> news_recoms
cd news_recoms
git remote set-url origin https://github.com/<твой-ник>/<твой-репо>.git
git push -u origin main
```

> ⚠️ Репозиторий должен быть **публичным**: GitHub Pages на бесплатном плане не публикует сайты
> из приватных репозиториев. Код открыт — это нормально, секретов в нём нет.

### Шаг 2. Включи GitHub Pages

`Settings → Pages → Source: GitHub Actions`

Больше ничего настраивать не нужно — workflow `Deploy Pages` сам соберёт и опубликует сайт
при каждом пуше в `main`.

### Шаг 3. Добавь ключ LLM

`Settings → Secrets and variables → Actions → New repository secret`

| Имя | Значение |
| :--- | :--- |
| `LLM_PROVIDERS_JSON` | JSON-массив провайдеров — готовый пример в [`.env.example`](.env.example) |
| `GEMINI_VIDEO_API_KEY` | *опционально* — глубокие саммари YouTube |
| `TELEGRAM_BOT_TOKEN` | *опционально* — алерты для постов 9–10/10 |
| `TELEGRAM_CHAT_ID` | *опционально* |

Минимум достаточно одного бесплатного ключа Gemini из [aistudio.google.com](https://aistudio.google.com).
Ключи Groq и OpenRouter добавь как резерв — роутер сам переключится при лимитах.

### Шаг 4. Запусти сбор вручную один раз

`Actions → Ingest Pipeline → Run workflow`

Первый запуск сам создаст ветку `data`, инициализирует в ней 4 JSON-файла с дефолтными
источниками и принесёт первые новости. Дальше он ходит по расписанию каждые 2 часа.

### Шаг 5. Открой сайт

`https://<твой-ник>.github.io/<твой-репо>/`

Лента сразу работает — читать можно без всякой настройки.

**Чтобы сохранять лайки и «Просмотрено»:**

1. Создай Fine-Grained Token: [github.com/settings/personal-access-tokens/new](https://github.com/settings/personal-access-tokens/new)
   - Repository access → **Only select repositories** → выбери свой репозиторий
   - Permissions → **Contents: Read and write**
   - (по желанию) **Actions: Read and write** — чтобы работала кнопка «Запустить сбор» в UI
2. На сайте: **Настройки → Синхронизация → вставь токен → Подключить**
3. Нажми **QR для телефона** и отсканируй код камерой — телефон подключится автоматически

> Токен хранится только в `localStorage` твоего браузера и никогда не уходит никуда, кроме
> `api.github.com`. Данные пишутся в **твой** репозиторий.

## Установка на телефон как приложение

- **Android / Chrome:** меню ⋮ → «Установить приложение»
- **iPhone / Safari:** «Поделиться» → «На экран „Домой“»

Появится иконка, полноэкранный режим, нижняя навигация и свайпы.

## Как добавить источники

**Настройки → Источники** (нужна подключённая синхронизация — изменения пишутся в `config.json`):

- **Telegram** — `@channel` или `https://t.me/channel` (канал должен быть публичным)
- **RSS** — полный URL фида. Тумблер `full` вытягивает полный текст через trafilatura
- **YouTube** — `UC...` channel_id, `@handle` или ссылка на канал (резолвится автоматически)

`channel_id` вручную: страница канала → `Ctrl+U` → поиск `channel_id=` → скопируй `UC...`

## Горячие клавиши (ПК)

`j`/`k` — навигация · `e` — просмотрено · `s` — отложить · `l` — лайк · `d` — дизлайк · `o` — открыть

На телефоне: свайп вправо — просмотрено, свайп влево — дизлайк.

## Как это работает внутри

1. **Сбор** — парсеры тянут посты из `t.me/s/*`, RSS и YouTube-фидов
2. **Слой 1** — regex-антиреклама (erid, #реклама, ИНН, промокод, «запишись на вебинар») + дедуп
   по нормализованному URL и SHA256 контента
3. **Слой 2** — LLM-роутер: скоринг 1–10, перевод на русский, выжимка, тезисы, `cluster_slug` для
   схлопывания сюжетов. При 429/503/401 провайдер уходит в cooldown, батч дробится
4. **Запись** — результат уходит в `feed.json` ветки `data` (коммит `chore: ingest update`)
5. **Обучение** — накопленные 👍/👎 (порог `learn_feedback_threshold`) переписывают `learned_profile`
6. **Чистка** — `rejected` старше 7 дней и `archived` старше 30 дней удаляются, `saved` — **никогда**.
   Файл всегда остаётся лёгким (лимит ~1000 элементов)

## Где взять бесплатные ключи

| Сервис | Ссылка | Что даёт |
| :--- | :--- | :--- |
| Google AI Studio | aistudio.google.com | Gemini Flash, 1M контекст, YouTube-видео |
| GroqCloud | console.groq.com | Мгновенный резерв, 30 RPM |
| OpenRouter | openrouter.ai | `:free` модели, третий эшелон |

## Локальная разработка

```bash
# Фронтенд с HMR
cd app && npm install && npm run dev        # http://127.0.0.1:5173

# Пайплайн
cd pipeline
cp ../.env.example .env                     # заполни LLM_PROVIDERS_JSON
python -m pytest
DRY_RUN=true python main.py --data-dir ../data_repo
```

Для локального фронтенда задай в `app/.env.local`:

```bash
VITE_GITHUB_OWNER=твой-ник
VITE_GITHUB_REPO=твой-репо
```

Иначе репозиторий определяется из `window.location.hostname` (для `*.github.io`).

Посмотреть на данные локально, не трогая репозиторий:

```bash
git clone --branch data <репо> data_repo
cd pipeline && DRY_RUN=true python main.py --data-dir ../data_repo
```

## Тесты

```bash
cd app && npm run typecheck && npm run build
cd pipeline && python -m pytest
```

Покрыто: regex-антиреклама (erid/#реклама/ИНН/промокод/битый regex), нормализация URL и хэши,
парсинг `t.me/s/` на HTML-фикстуре, failover LLM (429/503/401), cooldown,
**адаптивное дробление батча 30 → 8**, файловое хранилище (мерж `user_state` → `feed`, дедуп,
кластеры, чистка с сохранением `saved`, атомарная запись, roundtrip кириллицы).

## Структура

```text
news_recoms/
├── .github/workflows/
│   ├── ingest.yml           cron каждые 2 часа → пишет ветку data
│   └── deploy-pages.yml     push в main → GitHub Pages
├── app/                     React 19 + Vite + Tailwind PWA
│   ├── public/              manifest, sw.js, иконка
│   └── src/client/
│       ├── api/             github.ts (REST), store.ts (мерж+debounce), client.ts
│       └── components/      FeedList, ItemCard, SourcesManager, SyncModal, SyncSettings…
├── pipeline/                Python 3.12
│   ├── storage.py           Git-as-a-Database: 4 JSON-файла, атомарная запись
│   ├── parsers/             telegram, rss, youtube
│   ├── filters/             ad_filter, dedup
│   └── llm/                 router (failover), prompts, youtube_video
└── .env.example
```

## Заметки

- **YouTube:** не парсим субтитры с облачных IP (бан). Берём видео из RSS, а глубокое саммари —
  через Gemini по ссылке.
- **Прокси не нужен:** GitHub Actions ходит с зарубежных IP, гео-блоки Gemini/Groq не мешают.
- **pending_ai:** если легли все LLM — посты не теряются, дообрабатываются следующим кроном.
- **Порог ленты** (`min_inbox_score`, по умолчанию 6.5) и **порог алерта** (9.0) правятся в UI.
- **Лимит анонимного GitHub API** — 60 запросов/час. Приложение читает данные напрямую с
  `raw.githubusercontent.com` (без лимита), а API использует только когда есть токен.
