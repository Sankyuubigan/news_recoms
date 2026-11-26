from __future__ import annotations

import logging

from api_client import WorkerClient
from llm.prompts import PROFILE_SYSTEM_PROMPT, build_profile_user_prompt
from llm.router import AllProvidersFailed, LLMRouter
from models import ProfileUpdateResponse, SettingsBundle

log = logging.getLogger(__name__)


def run_learner(
    client: WorkerClient,
    router: LLMRouter,
    bundle: SettingsBundle,
) -> bool:
    """Слой 3: обновляет learned_profile по накопленным 👍/👎.

    Возвращает True, если профиль обновлён.
    """
    feedback = client.unprocessed_feedback()
    if len(feedback) < max(1, bundle.learn_feedback_threshold):
        log.info(
            "learner: skipped (%d feedback, threshold %d)",
            len(feedback),
            bundle.learn_feedback_threshold,
        )
        return False

    likes = [f for f in feedback if f.get("feedback") == "like"]
    dislikes = [f for f in feedback if f.get("feedback") == "dislike"]
    item_ids = [int(f["id"]) for f in feedback if isinstance(f.get("id"), int)]

    if not likes and not dislikes:
        return False

    try:
        result = router.complete_json(
            PROFILE_SYSTEM_PROMPT,
            build_profile_user_prompt(bundle.manual_rules, bundle.learned_profile, likes, dislikes),
            ProfileUpdateResponse,
        )
    except AllProvidersFailed as e:
        log.warning("learner: all providers failed: %s", e)
        return False

    profile = (result.updated_learned_profile or "").strip()
    if not profile:
        log.warning("learner: empty profile returned")
        return False

    client.update_profile(profile[:4000], item_ids)
    log.info("learner: profile updated (%d likes, %d dislikes) — %s", len(likes), len(dislikes), result.changelog[:200])
    return True
