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
    ProvidersConfigError,
    build_router_from_json,
    extract_json,
    key_fingerprint,
    validate_providers_json,
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


def make_json_unsupported_error() -> openai.BadRequestError:
    return openai.BadRequestError(
        "response_format of type json_object is not supported by this model",
        response=httpx.Response(400, request=_req()),
        body={"error": {"message": "response_format json_object not supported"}},
    )


def make_good_config(**overrides: Any) -> dict:
    """Валидная запись провайдера для тестов валидации."""
    base = {
        "name": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": "AIzaSyRealLookingKey123456",
        "model": "gemini-2.5-flash",
    }
    base.update(overrides)
    return base


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


class TestJsonModeDefault:
    def test_omitted_json_mode_defaults_to_json_object(self) -> None:
        """Не указал json_mode — должен получить json_object, а не молчаливый none."""
        p = Provider.from_dict(make_good_config())
        assert p.json_mode == "json_object"

    def test_dataclass_default_matches_from_dict_default(self) -> None:
        direct = Provider(name="x", base_url="https://x/v1", api_key="k", model="m")
        parsed = Provider.from_dict({"name": "x", "base_url": "https://x/v1", "api_key": "k", "model": "m"})
        assert direct.json_mode == parsed.json_mode == "json_object"

    def test_explicit_none_is_respected(self) -> None:
        p = Provider.from_dict(make_good_config(json_mode="none"))
        assert p.json_mode == "none"

    def test_json_mode_is_case_insensitive(self) -> None:
        p = Provider.from_dict(make_good_config(json_mode="  JSON_OBJECT "))
        assert p.json_mode == "json_object"

    def test_unsupported_json_mode_falls_back_to_json_object(self) -> None:
        p = Provider.from_dict(make_good_config(json_mode="json_schema"))
        assert p.json_mode == "json_object"

    def test_json_unsupported_retries_without_response_format(self) -> None:
        """Провайдер не умеет json_object — повторяем тот же запрос без response_format."""
        p = make_provider("picky", 5)
        p.json_mode = "json_object"
        router = LLMRouter([p])
        calls: list[dict] = []

        class _FakeCreate:
            def __init__(self, payload: str) -> None:
                self.payload = payload

            def __call__(self, **kwargs: Any) -> Any:
                calls.append(kwargs)
                if "response_format" in kwargs:
                    raise make_json_unsupported_error()
                return type(
                    "R",
                    (),
                    {
                        "choices": [
                            type("C", (), {"message": type("M", (), {"content": self.payload})()})()
                        ]
                    },
                )()

        class _FakeClient:
            def __init__(self, create: _FakeCreate) -> None:
                self.chat = type("Chat", (), {"completions": type("Completions", (), {"create": create})()})()

        payload = json.dumps({"results": ok_results(build_payload(2))})
        fake_client = _FakeClient(_FakeCreate(payload))

        with patch.object(router, "_client", return_value=fake_client):
            raw = router._raw_completion(p, "sys", "user")

        assert json.loads(raw)["results"]
        assert len(calls) == 2
        assert "response_format" in calls[0]
        assert "response_format" not in calls[1]
        assert p.json_mode == "none"  # режим выключен на весь прогон


class TestValidateProvidersJson:
    @staticmethod
    def _errors(raw: str) -> list[str]:
        return [p.message for p in validate_providers_json(raw) if p.is_error]

    @staticmethod
    def _warnings(raw: str) -> list[str]:
        return [p.message for p in validate_providers_json(raw) if not p.is_error]

    def test_empty_string_reports_missing_secret(self) -> None:
        errors = self._errors("")
        assert len(errors) == 1
        assert "LLM_PROVIDERS_JSON пуст" in errors[0]
        assert "Secret" in errors[0]

    def test_whitespace_reports_missing_secret(self) -> None:
        assert self._errors("   ")[0].startswith("LLM_PROVIDERS_JSON пуст")

    def test_empty_array_is_error(self) -> None:
        assert "пустой массив" in self._errors("[]")[0]

    def test_invalid_json_reports_position(self) -> None:
        errors = self._errors('[{"name": "a",}]')
        assert len(errors) == 1
        assert "невалидный JSON" in errors[0]
        assert "строка 1" in errors[0]

    def test_not_an_array(self) -> None:
        assert "JSON-МАССИВОМ" in self._errors('{"name": "a"}')[0]

    def test_wrapped_in_quotes_gets_specific_hint(self) -> None:
        """Частая ошибка: скопировали из .env вместе с обрамляющими кавычками."""
        body = json.dumps([make_good_config()])
        errors = self._errors(f"'{body}'")
        assert len(errors) == 1
        assert "обёрнут в кавычки" in errors[0]
        assert "GitHub Secrets" in errors[0]

    def test_pasted_whole_env_file_gets_specific_hint(self) -> None:
        """Вторая частая ошибка: в секрет вставили весь .env вместо значения."""
        env_file = (
            "# ============================================================\n"
            "# NewsRecoms — пример переменных окружения\n"
            "# ============================================================\n"
            "LLM_PROVIDERS_JSON='[]'\n"
        )
        errors = self._errors(env_file)
        assert len(errors) == 1
        assert "вставлен весь файл .env" in errors[0]
        assert "начинаться с «[»" in errors[0]

    def test_valid_config_has_no_problems(self) -> None:
        assert validate_providers_json(json.dumps([make_good_config()])) == []

    def test_missing_api_key(self) -> None:
        assert any("пустой api_key" in p for p in self._errors(json.dumps([make_good_config(api_key="")])))

    def test_placeholder_api_key_detected(self) -> None:
        errors = self._errors(json.dumps([make_good_config(api_key="YOUR_GEMINI_KEY_1")]))
        assert any("похож на заготовку" in p for p in errors)

    def test_missing_base_url(self) -> None:
        assert any("пустой base_url" in p for p in self._errors(json.dumps([make_good_config(base_url="")])))

    def test_base_url_without_scheme(self) -> None:
        errors = self._errors(json.dumps([make_good_config(base_url="api.groq.com/v1")]))
        assert any("http://" in p for p in errors)

    def test_missing_model(self) -> None:
        cfg = make_good_config()
        del cfg["model"]
        assert any("не указана модель" in p for p in self._errors(json.dumps([cfg])))

    def test_entry_not_object(self) -> None:
        assert any("должен быть объектом" in p for p in self._errors(json.dumps(["gemini"])))

    def test_batch_size_out_of_range(self) -> None:
        errors = self._errors(json.dumps([make_good_config(max_batch_size=0)]))
        assert any("max_batch_size" in p and "вне диапазона" in p for p in errors)

    def test_non_numeric_batch_size(self) -> None:
        assert any("должен быть числом" in p for p in self._errors(json.dumps([make_good_config(max_batch_size="много")])))

    def test_bad_json_mode(self) -> None:
        errors = self._errors(json.dumps([make_good_config(json_mode="json_schema")]))
        assert any("json_mode" in p and "не поддерживается" in p for p in errors)

    def test_duplicate_name_is_warning(self) -> None:
        cfg = json.dumps([make_good_config(), make_good_config()])
        assert any("уже встречалось" in p for p in self._warnings(cfg))
        assert self._errors(cfg) == []

    def test_duplicate_api_key_is_warning(self) -> None:
        cfg = json.dumps([make_good_config(name="a"), make_good_config(name="b")])
        assert any("уже используется" in p for p in self._warnings(cfg))
        assert self._errors(cfg) == []

    def test_unusual_base_url_is_warning(self) -> None:
        cfg = json.dumps([make_good_config(base_url="https://api.example.com/chat")])
        assert any("не похож на OpenAI" in p for p in self._warnings(cfg))
        assert self._errors(cfg) == []

    def test_all_problems_reported_at_once(self) -> None:
        """Юзер должен увидеть все ошибки за один прогон, а не по одной."""
        cfg = json.dumps(
            [
                make_good_config(name="a", api_key="", model=""),
                make_good_config(name="b", base_url="", max_batch_size=999),
            ]
        )
        assert len(self._errors(cfg)) >= 4

    def test_soft_warnings_do_not_block_startup(self) -> None:
        """Дубль имени и нестандартный base_url — предупреждения, не блокер."""
        cfg = json.dumps([make_good_config(name="a"), make_good_config(name="a")])
        assert self._errors(cfg) == []


class TestBuildRouterValidation:
    def test_config_error_lists_all_problems(self) -> None:
        raw = json.dumps([make_good_config(api_key=""), make_good_config(name="b", base_url="")])
        with pytest.raises(ProvidersConfigError) as ei:
            build_router_from_json(raw)
        assert len(ei.value.problems) >= 2
        assert "ошибк" in str(ei.value)

    def test_providers_config_error_is_value_error(self) -> None:
        # main.py ловит ValueError — иерархия должна сохраняться
        assert issubclass(ProvidersConfigError, ValueError)

    def test_placeholder_key_blocks_startup(self) -> None:
        raw = json.dumps([make_good_config(api_key="YOUR_GEMINI_KEY_1")])
        with pytest.raises(ProvidersConfigError):
            build_router_from_json(raw)


class TestDisabledPersistence:
    def test_auth_failure_appears_in_snapshot(self) -> None:
        p1, p2 = make_provider("primary", 30), make_provider("backup", 8)
        router = LLMRouter([p1, p2])
        fake = FakeCompletion([make_auth_error(), ok_results])

        with patch.object(router, "_raw_completion", side_effect=_raw_wrapper(router, fake)):
            router.score("sys", simple_prompt, build_payload(3))

        snap = router.disabled_snapshot()
        assert len(snap) == 1
        entry = snap[0]
        assert entry["name"] == "primary"
        assert entry["key_fp"] == key_fingerprint("test-key")
        assert "401" in entry["reason"] or "authentication" in entry["reason"]
        assert entry["until"] > entry["at"]
        # самого ключа в снимке быть не должно
        assert "test-key" not in json.dumps(snap)

    def test_snapshot_restored_on_next_run(self) -> None:
        p = make_provider("primary", 30)
        router = LLMRouter([p])
        router.states["primary"].disabled = True
        router.states["primary"].disabled_reason = "authentication failed"
        snap = {e["key"]: e for e in router.disabled_snapshot()}

        # новый прогон: тот же конфиг + снимок
        p2 = make_provider("primary", 30)
        router2 = LLMRouter([p2], disabled=snap)
        assert router2.states["primary"].disabled is True
        assert router2.states["primary"].disabled_reason == "authentication failed"
        assert router2.disabled_names() == ["primary"]

    def test_expired_disable_is_ignored(self) -> None:
        p = make_provider("primary", 30)
        stale = {
            p.state_key: {
                "key": p.state_key,
                "name": "primary",
                "reason": "authentication failed",
                "at": "2020-01-01T00:00:00Z",
                "until": "2020-01-02T00:00:00Z",
            }
        }
        router = LLMRouter([p], disabled=stale)
        assert router.states["primary"].disabled is False
        assert router.disabled_names() == []

    def test_disable_of_different_key_does_not_leak(self) -> None:
        """Сменили ключ — старая блокировка не должна его выключать."""
        old = Provider(name="gemini", base_url="https://g/v1", api_key="old-key", model="m")
        snap = {old.state_key: {"key": old.state_key, "name": "gemini", "reason": "auth", "until": "2999-01-01T00:00:00Z"}}

        new = Provider(name="gemini", base_url="https://g/v1", api_key="new-key", model="m")
        router = LLMRouter([new], disabled=snap)
        assert router.states["gemini"].disabled is False

    def test_model_idx_restored(self) -> None:
        p = Provider(
            name="g",
            base_url="https://g/v1",
            api_key="k",
            model="m1",
            models=["m1", "m2"],
        )
        snap = {
            p.state_key: {
                "key": p.state_key,
                "name": "g",
                "reason": "auth",
                "model": "m2",
                "until": "2999-01-01T00:00:00Z",
            }
        }
        router = LLMRouter([p], disabled=snap)
        assert router.states["g"].model_idx == 1
        assert router._current_model(p) == "m2"

    def test_snapshot_empty_when_all_alive(self) -> None:
        router = LLMRouter([make_provider("a", 5)])
        assert router.disabled_snapshot() == []


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
