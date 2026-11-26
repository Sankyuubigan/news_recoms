# NewsRecoms

Персональный AI-агрегатор контента: Telegram-каналы, RSS/сайты и YouTube → AI-фильтр мусора →
чистая лента в PWA на телефоне и ПК. **Хостинг $0, карта не нужна.**

- 🔥 **AI-скоринг 1–10** под твои правила + перевод + саммари + тезисы
- 🚫 **Двухслойная резка рекламы**: regex до нейронки (erid, #реклама, ИНН) + детект нативной рекламы в LLM
- 🧩 **Схлопывание сюжетов**: одна новость из 10 каналов → одна карточка со ссылками на все источники
- 🧠 **Самообучение**: 👍/👎 → LLM переписывает профиль твоих интересов
- 📥 **Inbox Zero**: Новое / Отложено / Архив / Мусор ИИ
- 🔁 **Smart Failover**: Gemini → Groq → OpenRouter, с авто-дроблением батча

## Архитектура

```text
[GitHub Actions — cron каждые 2 часа]
   │  парсит t.me/s/*, RSS, YouTube RSS
   │  Слой 1: regex-антиреклама + дедуп по URL/хэшу
   │  Слой 2: LLM-роутер (скоринг, перевод, выжимка, кластеры)
   │  Слой 3: обучение профиля по 👍/👎
   │  Слой 4: алерты 9–10/10 в Telegram
   ▼
[Cloudflare Worker + D1]  ← единый деплой: API + статика PWA
   ▲
[React PWA] свайпы, горячие клавиши, установка на телефон
```

Python **не ходит в D1 напрямую** — только через `/api/internal/*` с `Bearer INGEST_SECRET`.

## Быстрый старт

### 1. Клонируй и поставь зависимости

```bash
git clone <твой-репозиторий> news_recoms && cd news_recoms
cd app && npm install && cd ..
cd pipeline && pip install -r requirements.txt && cd ..
```

### 2. Создай D1 базу

```bash
cd app
npx wrangler login
npx wrangler d1 create news_recoms-db
```

Скопируй выведенный `database_id` в `app/wrangler.jsonc` вместо `REPLACE_WITH_YOUR_D1_DATABASE_ID`.

### 3. Примени миграции

```bash
npx wrangler d1 migrations apply news_recoms-db --remote
```

### 4. Задай секреты Worker

```bash
npx wrangler secret put APP_PASSWORD     # пароль для входа в PWA
npx wrangler secret put SESSION_SECRET   # любая длинная случайная строка
npx wrangler secret put INGEST_SECRET    # токен для пайплайна
```

Сгенерировать случайную строку:

```bash
openssl rand -hex 32
```

### 5. Задеплой

```bash
npm run build
npx wrangler deploy
```

Получишь URL вида `https://news_recoms.<subdomain>.workers.dev` — открывай, вводи `APP_PASSWORD`,
и добавляй на главный экран телефона (PWA).

### 6. Настрой GitHub Actions

Добавь в **Settings → Secrets and variables → Actions**:

| Secret | Значение |
| :--- | :--- |
| `WORKER_BASE_URL` | `https://news_recoms.<subdomain>.workers.dev` |
| `INGEST_SECRET` | тот же, что в шаге 4 |
| `LLM_PROVIDERS_JSON` | JSON-массив провайдеров (см. `.env.example`) |
| `GEMINI_VIDEO_API_KEY` | опционально — для глубоких саммари YouTube |
| `TELEGRAM_BOT_TOKEN` | опционально — алерты 9–10/10 |
| `TELEGRAM_CHAT_ID` | опционально |

Для авто-деплоя при пуше в `main` (workflow `deploy-app.yml`) добавь ещё:

| Secret | Где взять |
| :--- | :--- |
| `CLOUDFLARE_API_TOKEN` | dash.cloudflare.com → My Profile → API Tokens → Create Token (шаблон «Edit Cloudflare Workers») |
| `CLOUDFLARE_ACCOUNT_ID` | dash.cloudflare.com → Workers & Pages → правая панель |

### 7. Первый запуск

Actions → **Ingest Pipeline** → *Run workflow*. Через минуту в ленте появятся посты.

## Где взять бесплатные ключи

| Сервис | Ссылка | Что даёт |
| :--- | :--- | :--- |
| Google AI Studio | aistudio.google.com | Gemini Flash, 1M контекст, YouTube-видео |
| GroqCloud | console.groq.com | Мгновенный резерв, 30 RPM |
| OpenRouter | openrouter.ai | `:free` модели, третий эшелон |
| Cloudflare | dash.cloudflare.com | Worker + D1, карта не нужна |

## Как добавить источники

В PWA → **Настройки → Источники**:

- **Telegram** — `@channel` или `https://t.me/channel` (канал должен быть публичным)
- **RSS** — полный URL фида. Тумблер `full` вытягивает полный текст через trafilatura
- **YouTube** — `UC...` channel_id, `@handle` или ссылка на канал (резолвится автоматически)

`channel_id` вручную: страница канала → `Ctrl+U` → поиск `channel_id=` → скопируй `UC...`

## Горячие клавиши (ПК)

`j`/`k` — навигация · `e` — просмотрено · `s` — отложить · `l` — лайк · `d` — дизлайк · `o` — открыть

На телефоне: свайп вправо — просмотрено, свайп влево — дизлайк.

## Локальная разработка

```bash
# Worker API + статика
cd app && npm run build && npm run preview     # http://127.0.0.1:8787

# Фронтенд с HMR (проксирует /api на 8787)
cd app && npm run dev                          # http://127.0.0.1:5173

# Пайплайн
cd pipeline
cp ../.env.example .env    # заполни ключи
python -m pytest
DRY_RUN=true python main.py
```

Локальная D1:

```bash
cd app && npx wrangler d1 migrations apply news_recoms-db --local
```

## Тесты

```bash
cd app && npm run typecheck && npm run build
cd pipeline && python -m pytest
```

Покрыто: regex-антиреклама (erid/#реклама/ИНН/промокод/битый regex), нормализация URL и хэши,
парсинг `t.me/s/` на HTML-фикстуре, failover LLM (429/503/401), cooldown,
**адаптивное дробление батча 30 → 8**.

## Структура

```text
news_recoms/
├── .github/workflows/     ingest.yml (cron) + deploy-app.yml (push в main)
├── app/                   Cloudflare Worker (Hono) + React PWA
│   ├── migrations/        0001_init.sql — схема D1
│   └── src/worker/        auth (HMAC cookie), routes/{auth,feed,sources,settings,internal}
│   └── src/client/        App, FeedList, ItemCard, SourcesManager, RulesAndProfile, PipelineLogs
├── pipeline/              Python 3.12: парсеры, фильтры, LLM-роутер, learner, notifier
└── .env.example
```

## Заметки

- **YouTube:** не парсим субтитры с облачных IP (бан). Берём видео из RSS, а глубокое саммари —
  через Gemini по ссылке.
- **Прокси не нужен:** GitHub Actions ходит с зарубежных IP, гео-блоки Gemini/Groq не мешают.
- **pending_ai:** если легли все LLM — посты не теряются, дообрабатываются следующим кроном.
- **Порог ленты** (`min_inbox_score`, по умолчанию 6.5) и **порог алерта** (9.0) правятся в UI.
