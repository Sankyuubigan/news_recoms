from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import openai
from openai import OpenAI

from models import LLMResponse, LLMResultItem, ProfileUpdateResponse

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ provider


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    model: str
    max_batch_size: int = 8
    rpm_delay_sec: float = 3.0
    timeout_sec: float = 60.0
    json_mode: str = "json_object"  # json_object | json_schema | none

    @classmethod
    def from_dict(cls, d: dict) -> "Provider":
        return cls(
            name=str(d.get("name") or d.get("model") or "provider"),
            base_url=str(d.get("base_url") or ""),
            api_key=str(d.get("api_key") or ""),
            model=str(d.get("model") or ""),
            max_batch_size=int(d.get("max_batch_size") or 8),
            rpm_delay_sec=float(d.get("rpm_delay_sec") or 3.0),
            timeout_sec=float(d.get("timeout_sec") or 60.0),
            json_mode=str(d.get("json_mode") or "none"),
        )

    @property
    def usable(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


@dataclass
class ProviderState:
    cooldown_until: float = 0.0
    disabled: bool = False
    ok: int = 0
    err: int = 0
    last_call: float = 0.0
    dynamic_cap: int | None = None  # уменьшается при context-length ошибках

    def available(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        return not self.disabled and now >= self.cooldown_until


class AllProvidersFailed(Exception):
    """Все провайдеры недоступны — остаток уходит в pending_ai."""


# ------------------------------------------------------------------- helpers


def extract_json(raw: str) -> dict:
    """Устойчиво вытаскивает JSON-объект из ответа модели (снимает ```json обёртки)."""
    if not raw:
        raise ValueError("empty response")

    text = raw.strip()

    # ```json ... ``` или ``` ... ```
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"results": parsed}
    except json.JSONDecodeError:
        pass

    # Ищем первый сбалансированный объект
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"no valid JSON found in response: {text[:200]}")


def _classify_error(exc: Exception) -> str:
    """Классифицирует ошибку: auth | rate | overload | context | bad_json | other."""
    if isinstance(exc, ValueError):
        return "bad_json"
    if isinstance(exc, openai.AuthenticationError):
        return "auth"
    if isinstance(exc, openai.PermissionDeniedError):
        return "auth"
    if isinstance(exc, openai.RateLimitError):
        return "rate"
    if isinstance(exc, openai.BadRequestError):
        msg = str(exc).lower()
        if "context" in msg or "too long" in msg or "maximum" in msg:
            return "context"
        return "other"
    if isinstance(exc, openai.APIStatusError):
        status = getattr(exc, "status_code", 0) or 0
        if status in (500, 502, 503, 504):
            return "overload"
        return "other"
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return "overload"
    return "other"


# -------------------------------------------------------------------- router


class LLMRouter:
    """Multi-provider LLM router: failover, cooldown, adaptive batch splitting."""

    COOLDOWN_RATE = 60.0
    COOLDOWN_OVERLOAD = 20.0

    def __init__(self, providers: list[Provider]) -> None:
        self.providers = [p for p in providers if p.usable]
        if not self.providers:
            raise ValueError("no usable LLM providers configured")
        self.states: dict[str, ProviderState] = {p.name: ProviderState() for p in self.providers}
        self._clients: dict[str, OpenAI] = {}

    # ---------------------------------------------------------------- public

    def stats(self) -> dict[str, dict[str, int]]:
        return {name: {"ok": st.ok, "err": st.err} for name, st in self.states.items()}

    def score_posts(
        self,
        system_prompt: str,
        build_user_prompt: Callable[[list[dict]], str],
        posts_payload: list[dict],
        max_rounds: int = 40,
    ) -> dict[str, LLMResultItem]:
        """Оценивает посты, возвращает map temp_id -> LLMResultItem.

        Реализует adaptive batch splitting: если провайдер упал, остаток дробится
        под max_batch_size следующего живого провайдера; при context-length ошибке
        батч конкретного провайдера ужимается вдвое.
        """
        results: dict[str, LLMResultItem] = {}
        queue = list(posts_payload)
        rounds = 0

        while queue:
            rounds += 1
            if rounds > max_rounds:
                raise AllProvidersFailed(f"max rounds exceeded, {len(queue)} items left")

            provider = self._next_provider()
            if provider is None:
                raise AllProvidersFailed(f"{len(queue)} items left unprocessed")

            st = self.states[provider.name]
            cap = provider.max_batch_size if st.dynamic_cap is None else min(provider.max_batch_size, st.dynamic_cap)
            chunk_size = min(len(queue), max(1, cap))
            chunk = queue[:chunk_size]

            try:
                items = self._call_scoring(provider, system_prompt, build_user_prompt, chunk)
                for item in items:
                    results[item.temp_id] = item
                st.ok += 1
                queue = queue[chunk_size:]
            except AllProvidersFailed:
                raise
            except Exception as e:  # noqa: BLE001 — любая ошибка провайдера = failover
                kind = _classify_error(e)
                st.err += 1
                self._handle_failure(provider, kind)
                log.warning("provider %s failed (%s): %s", provider.name, kind, str(e)[:200])

                if kind == "context" and chunk_size > 1:
                    # ужимаем батч этого провайдера вдвое и пробуем снова
                    st.dynamic_cap = max(1, chunk_size // 2)
                # очередь не двигается — следующий живой провайдер возьмёт её (уже с его max_batch_size)
                continue

        return results

    def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model_cls: type,
    ) -> Any:
        """Одиночный JSON-вызов (learner и прочие) с тем же failover."""
        while True:
            provider = self._next_provider()
            if provider is None:
                raise AllProvidersFailed("no providers available for single call")
            try:
                raw = self._raw_completion(provider, system_prompt, user_prompt)
                data = extract_json(raw)
                self.states[provider.name].ok += 1
                return model_cls.model_validate(data)
            except Exception as e:  # noqa: BLE001
                kind = _classify_error(e)
                self.states[provider.name].err += 1
                self._handle_failure(provider, kind)
                log.warning("provider %s single call failed (%s): %s", provider.name, kind, str(e)[:200])

    # --------------------------------------------------------------- internal

    def _next_provider(self) -> Provider | None:
        now = time.monotonic()
        for p in self.providers:
            st = self.states[p.name]
            if st.available(now):
                return p
        return None

    def _handle_failure(self, provider: Provider, kind: str) -> None:
        st = self.states[provider.name]
        if kind == "auth":
            st.disabled = True
        elif kind == "rate":
            st.cooldown_until = time.monotonic() + self.COOLDOWN_RATE
        elif kind == "overload":
            st.cooldown_until = time.monotonic() + self.COOLDOWN_OVERLOAD
        elif kind == "context":
            # провайдер жив — просто батч слишком большой, cap ужимается отдельно
            pass
        elif kind == "bad_json":
            st.cooldown_until = time.monotonic() + 5.0
        else:
            st.cooldown_until = time.monotonic() + 10.0

    def _client(self, provider: Provider) -> OpenAI:
        if provider.name not in self._clients:
            self._clients[provider.name] = OpenAI(
                base_url=provider.base_url,
                api_key=provider.api_key,
                timeout=provider.timeout_sec,
                max_retries=0,
            )
        return self._clients[provider.name]

    def _throttle(self, provider: Provider) -> None:
        st = self.states[provider.name]
        now = time.monotonic()
        elapsed = now - st.last_call
        if elapsed < provider.rpm_delay_sec:
            time.sleep(provider.rpm_delay_sec - elapsed)
        st.last_call = time.monotonic()

    def _raw_completion(self, provider: Provider, system_prompt: str, user_prompt: str) -> str:
        self._throttle(provider)
        client = self._client(provider)

        kwargs: dict[str, Any] = {
            "model": provider.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        if provider.json_mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}

        res = client.chat.completions.create(**kwargs)
        content = res.choices[0].message.content if res.choices else None
        if not content:
            raise ValueError("empty completion")
        return content

    def _call_scoring(
        self,
        provider: Provider,
        system_prompt: str,
        build_user_prompt: Callable[[list[dict]], str],
        chunk: list[dict],
    ) -> list[LLMResultItem]:
        user_prompt = build_user_prompt(chunk)
        raw = self._raw_completion(provider, system_prompt, user_prompt)
        data = extract_json(raw)
        parsed = LLMResponse.model_validate(data)

        expected = {p["temp_id"] for p in chunk}
        got = {r.temp_id for r in parsed.results}
        if not parsed.results:
            raise ValueError("empty results array")
        if len(got & expected) < max(1, len(expected) // 2):
            raise ValueError(f"results mismatch: expected {len(expected)}, got {len(got)}")

        return [r for r in parsed.results if r.temp_id in expected]

    # ------------------------------------------------------------ convenience

    def score(
        self,
        system_prompt: str,
        build_user_prompt: Callable[[list[dict]], str],
        posts_payload: list[dict],
    ) -> dict[str, LLMResultItem]:
        return self.score_posts(system_prompt, build_user_prompt, posts_payload)


def build_router_from_json(raw_json: str) -> LLMRouter:
    try:
        data = json.loads(raw_json or "[]")
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM_PROVIDERS_JSON is not valid JSON: {e}") from e
    if not isinstance(data, list):
        raise ValueError("LLM_PROVIDERS_JSON must be a JSON array")
    providers = [Provider.from_dict(d) for d in data if isinstance(d, dict)]
    return LLMRouter(providers)
