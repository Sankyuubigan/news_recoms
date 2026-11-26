from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import httpx
import openai
import pytest

from llm.router import (
    AllProvidersFailed,
    LLMRouter,
    Provider,
    build_router_from_json,
    extract_json,
)


def _req() -> httpx.Request:
    return httpx.Request("POST", "https://example.com/v1/chat/completions")


def make_rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError(
        "rate limit exceeded",
        response=httpx.Response(429, request=_req()),
        body={"error": {"message": "rate limit"}},
    )


def make_overload_error() -> openai.APIStatusError:
    return openai.APIStatusError(
        "service overloaded",
        response=httpx.Response(503, request=_req()),
        body={"error": {"message": "overloaded"}},
    )


def make_auth_error() -> openai.AuthenticationError:
    return openai.AuthenticationError(
        "invalid api key",
        response=httpx.Response(401, request=_req()),
        body={"error": {"message": "unauthorized"}},
    )


def make_context_error() -> openai.BadRequestError:
    return openai.BadRequestError(
        "maximum context length exceeded",
        response=httpx.Response(400, request=_req()),
        body={"error": {"message": "maximum context length exceeded"}},
    )


class FakeCompletion:
    """Подменяет chat.completions.create, возвращая заданные ответы/исключения."""

    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls: list[list[dict]] = []

    def __call__(self, **kwargs: Any) -> str:
        messages = kwargs.get("messages", [])
        user_content = messages[-1]["content"] if messages else ""
        chunk = json.loads(user_content[user_content.find("[") :]) if "[" in user_content else []
        self.calls.append(chunk)

        action = self.script.pop(0) if self.script else None
        if isinstance(action, Exception):
            raise action
        if action is None:
            action = lambda c: c  # noqa: E731

        results = action(chunk) if callable(action) else action
        return json.dumps({"results": results})


def make_provider(name: str, batch: int = 30) -> Provider:
    return Provider(
        name=name,
        base_url=f"https://{name}.example.com/v1",
        api_key="test-key",
        model="test-model",
        max_batch_size=batch,
        rpm_delay_sec=0.0,
        timeout_sec=5.0,
    )


def build_payload(n: int) -> list[dict]:
    return [
        {"temp_id": f"item_{i}", "source_name": "S", "content_type": "telegram", "title": f"t{i}", "text": "x"}
        for i in range(n)
    ]


def ok_results(chunk: list[dict]) -> list[dict]:
    return [
        {
            "temp_id": p["temp_id"],
            "is_ad": False,
            "score": 8.0,
            "category": "ИИ",
            "tags": ["test"],
            "title_ru": "Заголовок",
            "summary_ru": "Выжимка",
            "key_points_ru": ["факт"],
            "ai_reason": "ок",
            "cluster_slug": "test-cluster",
        }
        for p in chunk
    ]


def simple_prompt(chunk: list[dict]) -> str:
    return "Оцени:\n" + json.dumps(chunk)


class TestExtractJson:
    def test_plain_json(self) -> None:
        assert extract_json('{"results": []}') == {"results": []}

    def test_fenced_json(self) -> None:
        assert extract_json('```json\n{"results": [1]}\n```') == {"results": [1]}

    def test_bare_fence(self) -> None:
        assert extract_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_json_with_preamble(self) -> None:
        assert extract_json('Вот результат: {"a": 1} — готово') == {"a": 1}

    def test_array_wrapped(self) -> None:
        assert extract_json("[1,2]") == {"results": [1, 2]}

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json("полностью не JSON")

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json("")


class TestFailover:
    def test_429_switches_to_next_provider(self) -> None:
        p1, p2 = make_provider("primary", 30), make_provider("backup", 8)
        router = LLMRouter([p1, p2])
        fake = FakeCompletion([make_rate_limit_error(), ok_results])

        with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, fake)):
            results = router.score("sys", simple_prompt, build_payload(5))

        assert len(results) == 5
        assert router.states["primary"].err == 1
        assert router.states["backup"].ok >= 1

    def test_503_switches_to_next_provider(self) -> None:
        p1, p2 = make_provider("primary", 30), make_provider("backup", 8)
        router = LLMRouter([p1, p2])
        fake = FakeCompletion([make_overload_error(), ok_results])

        with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, fake)):
            results = router.score("sys", simple_prompt, build_payload(3))

        assert len(results) == 3
        assert router.states["primary"].err == 1

    def test_401_disables_provider_for_run(self) -> None:
        p1, p2 = make_provider("primary", 30), make_provider("backup", 8)
        router = LLMRouter([p1, p2])
        fake = FakeCompletion([make_auth_error(), ok_results])

        with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, fake)):
            router.score("sys", simple_prompt, build_payload(3))

        assert router.states["primary"].disabled is True
        assert router.states["primary"].available() is False

    def test_rate_limit_sets_cooldown(self) -> None:
        p1, p2 = make_provider("primary", 30), make_provider("backup", 8)
        router = LLMRouter([p1, p2])
        fake = FakeCompletion([make_rate_limit_error(), ok_results])

        with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, fake)):
            router.score("sys", simple_prompt, build_payload(3))

        assert router.states["primary"].cooldown_until > 0
        assert router.states["primary"].available() is False


class TestAdaptiveBatchSplitting:
    def test_batch_30_splits_into_8_for_backup(self) -> None:
        """Ключевой кейс: 30 постов на Gemini упали → Groq берёт по 8."""
        p1, p2 = make_provider("gemini", 30), make_provider("groq", 8)
        router = LLMRouter([p1, p2])
        # первый вызов (30) падает, дальше все успешны
        fake = FakeCompletion([make_rate_limit_error()] + [ok_results] * 10)

        with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, fake)):
            results = router.score("sys", simple_prompt, build_payload(30))

        assert len(results) == 30
        # первый чанк = 30, затем 8,8,8,6
        sizes = [len(c) for c in fake.calls]
        assert sizes[0] == 30
        assert sizes[1:] == [8, 8, 8, 6]

    def test_all_providers_fail_raises(self) -> None:
        p1 = make_provider("only", 5)
        router = LLMRouter([p1])
        fake = FakeCompletion([make_auth_error()])

        with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, fake)):
            with pytest.raises(AllProvidersFailed):
                router.score("sys", simple_prompt, build_payload(3))

    def test_context_error_shrinks_batch(self) -> None:
        p1 = make_provider("p", 10)
        router = LLMRouter([p1])
        fake = FakeCompletion([make_context_error(), ok_results, ok_results])

        with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, fake)):
            results = router.score("sys", simple_prompt, build_payload(10))

        assert len(results) == 10
        # первый чанк 10, после context-ошибки cap стал 5
        assert [len(c) for c in fake.calls][:2] == [10, 5]


class TestBuildRouterFromJson:
    def test_valid_json(self) -> None:
        raw = json.dumps(
            [
                {
                    "name": "a",
                    "base_url": "https://a/v1",
                    "api_key": "k",
                    "model": "m",
                    "max_batch_size": 5,
                }
            ]
        )
        router = build_router_from_json(raw)
        assert len(router.providers) == 1
        assert router.providers[0].max_batch_size == 5

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ValueError):
            build_router_from_json("{not json")

    def test_empty_providers_raises(self) -> None:
        with pytest.raises(ValueError):
            build_router_from_json("[]")

    def test_unusable_provider_filtered(self) -> None:
        raw = json.dumps([{"name": "broken", "base_url": "", "api_key": "", "model": ""}])
        with pytest.raises(ValueError):
            build_router_from_json(raw)

    def test_duplicate_names_get_unique_state(self) -> None:
        """Несколько ключей одного провайдера: у каждого свой кулдаун и клиент."""
        raw = json.dumps(
            [
                {"name": "gemini", "base_url": "https://a/v1", "api_key": "k1", "model": "m"},
                {"name": "gemini", "base_url": "https://a/v1", "api_key": "k2", "model": "m"},
                {"name": "gemini", "base_url": "https://a/v1", "api_key": "k3", "model": "m"},
            ]
        )
        router = build_router_from_json(raw)

        names = [p.name for p in router.providers]
        assert names == ["gemini", "gemini#2", "gemini#3"]
        assert len(set(names)) == 3
        assert len(router.states) == 3

        # каждый провайдер получает свой клиент со своим ключом
        clients = [router._client(p) for p in router.providers]
        assert len({id(c) for c in clients}) == 3
        assert [p.api_key for p in router.providers] == ["k1", "k2", "k3"]


class TestModelFallback:
    def test_models_list_falls_back_on_model_gone(self) -> None:
        """Модель выпилена у провайдера — берём следующую модель того же ключа."""
        p = Provider(
            name="gemini",
            base_url="https://g/v1",
            api_key="k1",
            model="old-model",
            models=["old-model", "new-model"],
            max_batch_size=5,
            rpm_delay_sec=0.0,
            timeout_sec=5.0,
        )
        router = LLMRouter([p])
        gone = openai.NotFoundError(
            "model not found",
            response=httpx.Response(404, request=_req()),
            body={"error": {"message": "model not found"}},
        )
        fake = FakeCompletion([gone, ok_results])

        with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, fake)):
            result = router.score("sys", simple_prompt, build_payload(2))

        assert len(result) == 2
        assert router.states["gemini"].model_idx == 1
        assert router.states["gemini"].disabled is False
        # ключ не выключен — провайдер продолжает работать на новой модели
        assert router.states["gemini"].err == 1

    def test_all_models_gone_disables_provider(self) -> None:
        p = Provider(
            name="only",
            base_url="https://g/v1",
            api_key="k1",
            model="m1",
            models=["m1"],
            max_batch_size=5,
            rpm_delay_sec=0.0,
            timeout_sec=5.0,
        )
        router = LLMRouter([p])
        gone = openai.NotFoundError(
            "model not found",
            response=httpx.Response(404, request=_req()),
            body={"error": {"message": "model not found"}},
        )

        with pytest.raises(AllProvidersFailed):
            with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, FakeCompletion([gone]))):
                router.score("sys", simple_prompt, build_payload(2))

        assert router.states["only"].disabled is True

    def test_model_field_used_when_models_absent(self) -> None:
        p = Provider.from_dict(
            {"name": "x", "base_url": "https://x/v1", "api_key": "k", "model": "solo"}
        )
        assert p.model_list == ["solo"]

    def test_models_string_normalized_to_list(self) -> None:
        p = Provider.from_dict(
            {"name": "x", "base_url": "https://x/v1", "api_key": "k", "models": " a , b "}
        )
        # строку можно писать через запятую — удобно для секрета
        assert p.model_list == ["a", "b"]

    def test_models_list_without_model_field(self) -> None:
        p = Provider.from_dict(
            {"name": "x", "base_url": "https://x/v1", "api_key": "k", "models": ["m1", "m2"]}
        )
        assert p.model_list == ["m1", "m2"]
        assert p.model == "m1"
        assert p.usable is True


class TestStats:
    def test_stats_tracked(self) -> None:
        p1, p2 = make_provider("p1", 5), make_provider("p2", 5)
        router = LLMRouter([p1, p2])
        fake = FakeCompletion([make_rate_limit_error(), ok_results])

        with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, fake)):
            router.score("sys", simple_prompt, build_payload(3))

        stats = router.stats()
        assert stats["p1"]["err"] == 1
        assert stats["p2"]["ok"] == 1


def _raw_wrapper(router: LLMRouter, fake: FakeCompletion):
    """Связывает подмену с реальным построением user_prompt внутри роутера."""

    def _inner(provider: Provider, system_prompt: str, user_prompt: str) -> str:
        return fake(messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}])

    return _inner
