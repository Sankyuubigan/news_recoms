from __future__ import annotations

SYSTEM_PROMPT = """Ты — экспертный фильтр персональной новостной ленты для разработчика ПО.
Твоя задача: оценить каждый пост, отсеять мусор и рекламу, сделать качественную выжимку.

ПРАВИЛА ОЦЕНКИ (score 1.0–10.0):
- 9.0–10.0: прорывные новости индустрии, глубокие технические разборы, релизы важных инструментов.
- 7.5–8.9: качественный контент, полезные гайды, интересные инженерные решения.
- 6.5–7.4: нормально, но без глубины; краткие новости по теме.
- 4.0–6.4: слабо, поверхностно, общие слова.
- 1.0–3.9: кликбейт, вода, перепост без сути, не по теме.

ЖЁСТКИЕ ПРАВИЛА:
1. Реклама, прогревы, курсы, вебинары, партнёрские подборки, промокоды — это is_ad: true и score: 1.0,
   даже если реклама «нативная» и замаскирована под полезный пост.
2. НЕ ВЫДУМЫВАЙ факты, которых нет в исходном тексте.
3. Все текстовые поля (title_ru, summary_ru, key_points_ru, ai_reason) — ТОЛЬКО на русском языке,
   даже если исходник на английском (переводи качественно, не транслитерируй).
4. Если новость описывает то же событие, что и другой пост в этом батче или в списке
   recent_clusters — присвой им ОДИНАКОВЫЙ cluster_slug (короткий латинский kebab-case).
5. summary_ru — плотная выжимка сути на 2–4 предложения, без воды и вступлений.
6. key_points_ru — 2–4 конкретных факта/цифры/вывода.
7. ai_reason — одно предложение, почему такая оценка.
8. title_ru — очищенный от кликбейта заголовок (без «ШОК», «ТЫ НЕ ПОВЕРИШЬ» и т.п.).

Отвечай СТРОГО валидным JSON-объектом без markdown-обёрток:
{"results": [{"temp_id": "...", "is_ad": false, "ad_reason": null, "score": 0.0,
"category": "...", "tags": ["..."], "title_ru": "...", "summary_ru": "...",
"key_points_ru": ["..."], "ai_reason": "...", "cluster_slug": "..."}]}

Поле temp_id должно точно совпадать с temp_id входного поста.
Верни результат для КАЖДОГО входного поста, ничего не пропускай."""


def build_scoring_user_prompt(
    manual_rules: str,
    learned_profile: str,
    posts_payload: list[dict],
    recent_clusters: list[dict] | None = None,
) -> str:
    import json

    parts: list[str] = []

    parts.append("## Мои ручные правила (приоритет №1)\n" + (manual_rules.strip() or "—"))

    if learned_profile.strip():
        parts.append("## Профиль, выученный на моих реакциях (уточняет правила)\n" + learned_profile.strip())

    if recent_clusters:
        recent = [
            {"cluster_slug": c.get("cluster_slug"), "title": c.get("title_ru")}
            for c in recent_clusters[:60]
            if c.get("cluster_slug")
        ]
        if recent:
            parts.append(
                "## Недавние сюжеты за 48ч (если пост про то же событие — используй тот же cluster_slug)\n"
                + json.dumps(recent, ensure_ascii=False)
            )

    parts.append(
        "## Посты для оценки\n"
        + json.dumps(posts_payload, ensure_ascii=False)
        + "\n\nВерни JSON с результатом для каждого temp_id."
    )

    return "\n\n".join(parts)


PROFILE_SYSTEM_PROMPT = """Ты обновляешь профиль интересов пользователя на основе его лайков и дизлайков.
Тебе даны: ручные правила пользователя, текущий выученный профиль и новые реакции.

Задача: вернуть обновлённый компактный профиль (до 1500 символов), который:
- обобщает, что пользователь РЕАЛЬНО лайкает (темы, формат, глубина, источники);
- фиксирует паттерны, которые он дизлайкает (с причинами, если указаны);
- не противоречит ручным правилам (они приоритетнее);
- написан как инструкция для фильтра ленты, без воды.

Отвечай СТРОГО валидным JSON:
{"updated_learned_profile": "...", "changelog": "кратко, что изменилось"}"""


def build_profile_user_prompt(
    manual_rules: str,
    learned_profile: str,
    likes: list[dict],
    dislikes: list[dict],
) -> str:
    import json

    def brief(items: list[dict]) -> list[dict]:
        out = []
        for it in items[:40]:
            out.append(
                {
                    "title": it.get("title"),
                    "category": it.get("category"),
                    "tags": it.get("tags"),
                    "reason": it.get("feedback_reason"),
                    "ai_reason": it.get("ai_reason"),
                }
            )
        return out

    return "\n\n".join(
        [
            "## Ручные правила (приоритет)\n" + (manual_rules.strip() or "—"),
            "## Текущий выученный профиль\n" + (learned_profile.strip() or "—"),
            "## Лайки\n" + json.dumps(brief(likes), ensure_ascii=False),
            "## Дизлайки\n" + json.dumps(brief(dislikes), ensure_ascii=False),
            "Верни обновлённый профиль JSON-объектом.",
        ]
    )


YOUTUBE_SUMMARY_PROMPT = """Посмотри это видео и сделай выжимку на русском языке.

Верни СТРОГО валидным JSON:
{"summary_ru": "плотная выжимка содержания на 3-5 предложений",
 "key_points_ru": ["ключевой момент 1 (таймкод MM:SS)", "ключевой момент 2 (таймкод MM:SS)", "..."]}

Требования:
- Только то, что реально сказано в видео. Не выдумывай.
- В key_points_ru указывай таймкоды ключевых моментов.
- 3–6 пунктов, каждый по делу."""
