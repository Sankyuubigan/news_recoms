"""Нагрузочный тест Gemini: тянет ли модель РЕАЛЬНУЮ работу.

Первый зонд показал: `gemini-3.5-flash` висит (ReadTimeout), а
`gemini-3.5-flash-lite` отдаёт 200 подряд. Но то были крошечные
запросы. Настоящий вопрос другой: выдержит ли flash-lite наш
РЕАЛЬНЫЙ системный промпт (~2k токенов) и батч из 20 постов —
и с какой задержкой.

Зонд делает N последовательных запросов ровно тем же промптом,
что уходит в проде, и печатает по каждому: код, время, сколько
постов модель вернула. Если flash-lite держит батч 20 — значит
его и ставим первым, а flash уходит в резерв.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time

import httpx

BASE = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

# Тот же системный промпт, что в llm/prompts.py (сокращать нельзя —
# именно его длина определяет расход TPM).
SYSTEM = """Ты — экспертный фильтр персональной новостной ленты для разработчика ПО.
Твоя задача: оценить каждый пост, отсеять мусор и рекламу, сделать качественную выжимку.

ПРАВИЛА ОЦЕНКИ (score 1.0–10.0):
- 9.0–10.0: прорывные новости индустрии, глубокие технические разборы, релизы важных инструментов.
- 7.5–8.9: качественный контент, полезные гайды, интересные инженерные решения.
- 6.5–7.4: нормально, но без глубины; краткие новости по теме.
- 4.0–6.4: слабо, поверхностно, общие слова.
- 1.0–3.9: кликбейт, вода, перепост без сути, не по теме.

ЖЁСТКИЕ ПРАВИЛА:
1. Реклама, прогревы, курсы, вебинары, партнёрские подборки, промокоды — это is_ad: true и score: 1.0.
2. НЕ ВЫДУМЫВАЙ факты, которых нет в исходном тексте.
3. Все текстовые поля — ТОЛЬКО на русском языке.
4. Если новость описывает то же событие, что и другой пост — присвой ОДИНАКОВЫЙ cluster_slug.
5. summary_ru — плотная выжимка сути на 2–4 предложения.
6. key_points_ru — 2–4 конкретных факта/цифры/вывода.
7. ai_reason — одно предложение, почему такая оценка.
8. title_ru — очищенный от кликбейта заголовок.

Отвечай СТРОГО валидным JSON-объектом без markdown-обёрток:
{"results": [{"temp_id": "...", "is_ad": false, "ad_reason": null, "score": 0.0,
"category": "...", "tags": ["..."], "title_ru": "...", "summary_ru": "...",
"key_points_ru": ["..."], "ai_reason": "...", "cluster_slug": "..."}]}

Поле temp_id должно точно совпадать с temp_id входного поста.
Верни результат для КАЖДОГО входного поста, ничего не пропускай."""

TITLES = [
    "Вышел Rust 1.90: стабилизирован async trait",
    "PostgreSQL 18 приносит инкрементальный бэкап",
    "Разбор: как устроен планировщик в Go 1.25",
    "Kubernetes 1.34 убрал dockershim окончательно",
    "Новый фреймворк для WASM на Rust",
    "OpenAI показал новую модель с 1M контекста",
    "Как мы уменьшили время сборки в 10 раз",
    "Критическая уязвимость в популярной библиотеке",
    "Гайд по профилированию памяти в Python",
    "SQLite научился работать в браузере",
    "Сравнение Kafka и Redpanda в проде",
    "История одного бага в распределённой системе",
    "TypeScript 6.0 с новым компилятором",
    "Как я написал свой менеджер пакетов",
    "Бенчмарки нового SSD на 10 ТБ",
    "Оптимизация запросов в ClickHouse",
    "Docker без демона: что нового",
    "Разбор архитектуры Git",
    "Эволюция протокола HTTP",
    "Что не так с микросервисами в 2026",
]


def build_payload(n: int = 20) -> list[dict]:
    posts = []
    for i in range(n):
        posts.append(
            {
                "temp_id": f"p{i}",
                "source": "Habr",
                "title": TITLES[i % len(TITLES)],
                "content": (TITLES[i % len(TITLES)] + ". ") * 12,
            }
        )
    return posts


def user_prompt(posts: list[dict]) -> str:
    return (
        "## Мои ручные правила (приоритет №1)\n"
        "Интересно: разработка ПО, архитектура, ИИ/LLM, инженерия.\n\n"
        "## Посты для оценки\n"
        + json.dumps(posts, ensure_ascii=False)
        + "\n\nВерни JSON с результатом для каждого temp_id."
    )


def run(model: str, key: str, rounds: int, batch: int) -> None:
    print(f"\n=== {model} | батч {batch} | {rounds} раундов ===")
    posts = build_payload(batch)
    latencies = []
    ok = 0
    for i in range(rounds):
        t0 = time.monotonic()
        try:
            r = httpx.post(
                BASE,
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": user_prompt(posts)},
                    ],
                    "temperature": 0,
                    "max_tokens": 8000,
                },
                timeout=90.0,
            )
        except Exception as e:  # noqa: BLE001
            dt = time.monotonic() - t0
            print(f"  [{i+1}] EXC {type(e).__name__} за {dt:.1f}с — {str(e)[:80]}")
            latencies.append(dt)
            continue

        dt = time.monotonic() - t0
        latencies.append(dt)

        if r.status_code == 200:
            ok += 1
            try:
                data = r.json()
                txt = data["choices"][0]["message"]["content"]
                parsed = json.loads(txt)
                got = len(parsed.get("results") or [])
                usage = data.get("usage") or {}
                print(
                    f"  [{i+1}] 200 за {dt:.1f}с — постов вернул {got}/{batch}, "
                    f"токенов in={usage.get('prompt_tokens')} out={usage.get('completion_tokens')}"
                )
            except Exception as e:  # noqa: BLE001
                print(f"  [{i+1}] 200 за {dt:.1f}с, но JSON битый: {type(e).__name__}: {str(e)[:80]}")
        else:
            body = r.text[:200]
            print(f"  [{i+1}] {r.status_code} за {dt:.1f}с — {body}")

    if latencies:
        print(
            f"  ИТОГ: {ok}/{rounds} успешных, медиана {statistics.median(latencies):.1f}с, "
            f"макс {max(latencies):.1f}с"
        )


def main() -> int:
    key1 = os.environ.get("GEMINI_KEY_1", "").strip()
    if not key1:
        print("нет GEMINI_KEY_1")
        return 1

    # flash-lite — кандидат в основные: батч 20, пять раундов.
    run("gemini-3.5-flash-lite", key1, rounds=5, batch=20)
    # Для сравнения — flash тем же батчем, но только 2 раунда (он висит).
    run("gemini-3.5-flash", key1, rounds=2, batch=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
