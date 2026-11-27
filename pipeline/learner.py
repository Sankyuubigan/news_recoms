from __future__ import annotations

import logging

from llm.prompts import PROFILE_SYSTEM_PROMPT, build_profile_user_prompt
from llm.router import AllProvidersFailed, LLMRouter
from models import ContentKind, ProfileUpdateResponse, SettingsBundle
from storage import Storage

log = logging.getLogger(__name__)

# Адаптивный порог. Пока профиль — дефолтная заглушка, ждём мало реакций, чтобы
# обучение вообще началось в первые дни. После первого реального обновления
# планка выше: каждое следующее обновление должно опираться на заметную выборку,
# иначе профиль будет дёргаться от одного случайного лайка.
LEARN_THRESHOLD_COLD = 5
LEARN_THRESHOLD_STEADY = 8

# Начала дефолтных профилей из storage.DEFAULT_SETTINGS: по ним определяем,
# что обучение ещё ни разу не происходило.
_PROFILE_STUBS = (
    "Пока недостаточно реакций пользователя для формирования",
    "Пока недостаточно реакций пользователя по репозиториям",
)


def _is_stub_profile(profile: str) -> bool:
    text = (profile or "").strip()
    return not text or any(text.startswith(stub) for stub in _PROFILE_STUBS)


def _effective_threshold(configured: int, learned_profile: str) -> int:
    """Холодный старт — мягче настройки, устоявшийся режим — не ниже неё."""
    if _is_stub_profile(learned_profile):
        return max(1, min(configured, LEARN_THRESHOLD_COLD))
    return max(configured, LEARN_THRESHOLD_STEADY)


def _learn_for_kind(
    storage: Storage,
    router: LLMRouter,
    kind: ContentKind,
    manual_rules: str,
    learned_profile: str,
    threshold: int,
) -> bool:
    effective = _effective_threshold(threshold, learned_profile)
    feedback = storage.unprocessed_feedback(kind=kind)
    if len(feedback) < effective:
        log.info(
            "learner[%s]: skipped (%d feedback, threshold %d, %s)",
            kind,
            len(feedback),
            effective,
            "cold start" if _is_stub_profile(learned_profile) else "steady",
        )
        return False

    likes = [f for f in feedback if f.get("feedback") == "like"]
    dislikes = [f for f in feedback if f.get("feedback") == "dislike"]
    item_ids = [str(f["id"]) for f in feedback if f.get("id")]

    if not likes and not dislikes:
        return False

    try:
        result = router.complete_json(
            PROFILE_SYSTEM_PROMPT,
            build_profile_user_prompt(manual_rules, learned_profile, likes, dislikes),
            ProfileUpdateResponse,
        )
    except AllProvidersFailed as e:
        log.warning("learner[%s]: all providers failed: %s", kind, e)
        return False

    profile = (result.updated_learned_profile or "").strip()
    if not profile:
        log.warning("learner[%s]: empty profile returned", kind)
        return False

    storage.update_profile(profile[:4000], item_ids, kind=kind)
    log.info(
        "learner[%s]: profile updated (%d likes, %d dislikes) — %s",
        kind,
        len(likes),
        len(dislikes),
        result.changelog[:200],
    )
    return True


def run_learner(
    storage: Storage,
    router: LLMRouter,
    bundle: SettingsBundle,
) -> bool:
    """Слой 3: независимо обновляет профили интересов для news и repos."""
    updated_news = _learn_for_kind(
        storage,
        router,
        kind="news",
        manual_rules=bundle.manual_rules,
        learned_profile=bundle.learned_profile,
        threshold=bundle.learn_feedback_threshold,
    )
    updated_repos = _learn_for_kind(
        storage,
        router,
        kind="repos",
        manual_rules=bundle.repo_rules,
        learned_profile=bundle.repo_learned_profile,
        threshold=bundle.learn_feedback_threshold,
    )
    return updated_news or updated_repos