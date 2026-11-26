from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import openai
from openai import OpenAI

from models import LLMResponse, LLMResultItem, ProfileUpdateResponse

log = logging.getLogger(__name__)

# Поддерживаемые режимы ответа. json_schema сознательно не реализован: без
# схемы в запросе он эквивалентен none, а молча деградировать — хуже, чем сказать.
JSON_MODES = ("json_object", "none")

# Признаки того, что в api_key забыли подставить реальный ключ.
_PLACEHOLDER_MARKERS = (
    "your_",
    "your-",
    "xxx",
    "changeme",
    "change_me",
    "placeholder",
    "api_key_here",
    "insert_",
    "вставь",
    "<",
    ">",
)


def key_fingerprint(api_key: str) -> str:
    """Необратимый отпечаток ключа: в config.json не должно попадать самого ключа."""
    return hashlib.sha256((api_key or "").encode("utf-8")).hexdigest()[:12]


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_after(hours: float) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    json_mode: str = "json_object"  # json_object | none
    # Модели в порядке приоритета. Если пусто — используется единственная `model`.
    # Нужно для фолбэка, когда модель устарела/выпилена у провайдера, а ключ жив.
    models: list[str] = field(default_factory=list)
    # Имя ДО авто-переименования дублей (name#2, name#3). По нему провайдер
    # опознаётся в config.json между прогонами.
    original_name: str = ""
    # Отпечаток api_key — чтобы отличить «тот же ключ» от «ключ заменили».
    key_fp: str = ""

    def __post_init__(self) -> None:
        if not self.original_name:
            self.original_name = self.name
        if not self.key_fp:
            self.key_fp = key_fingerprint(self.api_key)

    @classmethod
    def from_dict(cls, d: dict) -> "Provider":
        raw_models = d.get("models")
        models: list[str] = []
        if isinstance(raw_models, list):
            models = [str(m).strip() for m in raw_models if str(m).strip()]
        elif isinstance(raw_models, str) and raw_models.strip():
            # Строкой удобно писать в секрете: "gemini-2.5-flash, gemini-2.5-flash-lite"
            models = [m.strip() for m in raw_models.split(",") if m.strip()]

        single = str(d.get("model") or "").strip()
        # `model` — это первая модель, если `models` не задан явно
        if single and single not in models:
            models.insert(0, single)

        return cls(
            name=str(d.get("name") or single or "provider").strip(),
            base_url=str(d.get("base_url") or "").strip(),
            api_key=_resolve_api_key(d),
            model=single or (models[0] if models else ""),
            max_batch_size=_int_or(d.get("max_batch_size"), 8),
            rpm_delay_sec=_float_or(d.get("rpm_delay_sec"), 3.0),
            timeout_sec=_float_or(d.get("timeout_sec"), 60.0),
            # Дефолт обязан совпадать с дефолтом датакласса, иначе не указавший
            # json_mode пользователь молча получает regex-парсинг вместо JSON-режима.
            json_mode=_normalize_json_mode(d.get("json_mode")),
            models=models,
        )

    @property
    def model_list(self) -> list[str]:
        """Модели в порядке приоритета (никогда не пустой, если модель задана)."""
        ordered = [m for m in self.models if m]
        if ordered:
            return ordered
        return [self.model] if self.model else []

    @property
    def usable(self) -> bool:
        return bool(self.base_url and self.api_key and self.model_list)

    @property
    def state_key(self) -> str:
        """Ключ состояния: имя + отпечаток ключа (два ключа под одним именем не сливаются)."""
        return f"{self.original_name}|{self.key_fp}"

    def label(self) -> str:
        """Человекочитаемое имя для логов и errors[] — без утечки ключа."""
        return f"{self.name} ({self.key_fp})"


def _resolve_api_key(d: dict) -> str:
    """Ключ из `api_key` или из переменной окружения, названной в `api_key_env`.

    Второй вариант — основной для GitHub Actions: структура провайдеров лежит
    открытым файлом в репозитории (её правят в веб-интерфейсе), а сами ключи
    остаются в Secrets короткими значениями по одному на провайдера.
    """
    direct = str(d.get("api_key") or "").strip()
    if direct:
        return direct

    env_name = str(d.get("api_key_env") or "").strip()
    if not env_name:
        return ""
    return os.environ.get(env_name, "").strip()


def _int_or(value: Any, default: int) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_json_mode(value: Any) -> str:
    """json_mode из конфига → значение, которое реально понимает _raw_completion.

    Валидатор ловит json_schema как ошибку, но Provider должен оставаться
    работоспособным и при прямом конструировании: неизвестный режим = json_object.
    """
    mode = str(value or "json_object").strip().lower()
    return mode if mode in JSON_MODES else "json_object"


def _float_or(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class ProviderState:
    cooldown_until: float = 0.0
    disabled: bool = False
    ok: int = 0
    err: int = 0
    last_call: float = 0.0
    dynamic_cap: int | None = None  # уменьшается при context-length ошибках
    model_idx: int = 0  # текущая модель в Provider.model_list
    # Почему выключен — чтобы это попало в config.json и было видно в логах.
    disabled_reason: str = ""

    def available(self, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        return not self.disabled and now >= self.cooldown_until


class AllProvidersFailed(Exception):
    """Все провайдеры недоступны — остаток уходит в pending_ai."""


class ProvidersConfigError(ValueError):
    """LLM_PROVIDERS_JSON невалиден. `problems` — по одной причине на элемент."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems) or ["неизвестная ошибка конфигурации"]
        body = "\n".join(f"  {i}. {p}" for i, p in enumerate(self.problems, 1))
        super().__init__(f"LLM_PROVIDERS_JSON: {len(self.problems)} ошибк(и)\n{body}")


# ---------------------------------------------------------------- validation


def _looks_like_placeholder(key: str) -> bool:
    low = key.strip().lower()
    if not low:
        return True
    if low in {"key", "token", "none", "null", "test", "test-key", "test_key"}:
        return True
    return any(marker in low for marker in _PLACEHOLDER_MARKERS)


@dataclass(frozen=True)
class Problem:
    """Найденная проблема конфига. `error` блокирует запуск, `warning` — только лог."""

    severity: str  # "error" | "warning"
    message: str

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


def validate_providers_json(raw_json: str) -> list[Problem]:
    """Проверяет LLM_PROVIDERS_JSON и возвращает ВСЕ найденные проблемы.

    Разделение на error/warning принципиально: опечатка в api_key должна
    остановить запуск, а дубль имени — только предупредить (это легальный
    сценарий «несколько ключей одного вендора»).

    Сообщения — для человека: они попадают в runs.json → «Настройки → Логи запусков».
    """
    problems: list[Problem] = []

    def err(msg: str) -> None:
        problems.append(Problem("error", msg))

    def warn(msg: str) -> None:
        problems.append(Problem("warning", msg))

    raw = (raw_json or "").strip()
    if not raw or raw in ("null", '""', "''"):
        return [
            Problem(
                "error",
                "LLM_PROVIDERS_JSON пуст — добавь секрет репозитория (Settings → Secrets and "
                "variables → Actions → New repository secret) с JSON-массивом провайдеров, "
                "образец в .env.example",
            )
        ]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        # Самая частая ошибка при переносе из .env в GitHub Secrets: скопировали
        # вместе с обрамляющими одинарными кавычками. Говорим об этом прямо.
        if raw[:1] in ("'", '"') and raw[-1:] == raw[:1] and len(raw) > 2:
            return [
                Problem(
                    "error",
                    f"LLM_PROVIDERS_JSON обёрнут в кавычки ({raw[0]}...{raw[-1]}). "
                    f"В .env так писать можно, но в GitHub Secrets значение вставляется "
                    f"БЕЗ внешних кавычек: значение должно начинаться с «[» и заканчиваться «]»",
                )
            ]

        # Вторая частая ошибка: вставили весь файл .env целиком, а не одно значение.
        looks_like_env_file = "LLM_PROVIDERS_JSON" in raw or raw.lstrip().startswith("#")
        if looks_like_env_file:
            return [
                Problem(
                    "error",
                    "в секрет вставлен весь файл .env (или его фрагмент), а нужно только ОДНО "
                    "значение. Секрет должен начинаться с «[» и заканчиваться «]» — это JSON-массив "
                    "провайдеров. Всё, что до «[» (комментарии, «LLM_PROVIDERS_JSON=») и после «]», "
                    "нужно убрать",
                )
            ]

        snippet = raw[max(0, e.pos - 40) : e.pos + 40].replace("\n", " ")
        return [
            Problem(
                "error",
                f"LLM_PROVIDERS_JSON — невалидный JSON: {e.msg} (строка {e.lineno}, колонка {e.colno}). "
                f"Фрагмент около ошибки: «{snippet}». Частые причины: висячая запятая, одинарные "
                f"кавычки вместо двойных, переводы строк внутри строки",
            )
        ]

    if not isinstance(data, list):
        return [
            Problem(
                "error",
                f"LLM_PROVIDERS_JSON должен быть JSON-МАССИВОМ объектов, а получен "
                f"{type(data).__name__}. Оберни провайдеров в квадратные скобки: [{{...}}, {{...}}]",
            )
        ]

    if not data:
        return [Problem("error", "LLM_PROVIDERS_JSON — пустой массив, нет ни одного провайдера")]

    seen_names: dict[str, int] = {}
    seen_keys: dict[str, int] = {}

    for idx, entry in enumerate(data):
        pos = f"провайдер #{idx + 1}"
        if not isinstance(entry, dict):
            err(f"{pos}: должен быть объектом {{...}}, а не {type(entry).__name__}")
            continue

        name = str(entry.get("name") or "").strip()
        if name:
            pos = f"провайдер #{idx + 1} «{name}»"

        # --- обязательные поля
        base_url = str(entry.get("base_url") or "").strip()
        if not base_url:
            err(f"{pos}: пустой base_url — укажи OpenAI-совместимый адрес, например https://api.groq.com/openai/v1")
        elif not base_url.startswith(("http://", "https://")):
            err(f"{pos}: base_url должен начинаться с http:// или https:// (сейчас «{base_url}»)")
        elif not base_url.rstrip("/").endswith(("/v1", "/openai", "/v1beta/openai")):
            # Не ошибка, но частая причина 404 — предупреждаем.
            warn(
                f"{pos}: base_url «{base_url}» не похож на OpenAI-совместимый (обычно оканчивается на /v1). "
                f"Если провайдер работает — проигнорируй это предупреждение"
            )

        api_key = str(entry.get("api_key") or "").strip()
        env_name = str(entry.get("api_key_env") or "").strip()

        if not api_key and not env_name:
            err(
                f"{pos}: нет ключа — укажи \"api_key\": \"...\" напрямую или "
                f"\"api_key_env\": \"ИМЯ_СЕКРЕТА\", чтобы ключ брался из переменной окружения"
            )
        elif env_name:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
                err(
                    f"{pos}: api_key_env «{env_name}» — недопустимое имя переменной "
                    f"(латиница, цифры и _, не начинается с цифры)"
                )
            elif not os.environ.get(env_name, "").strip():
                err(
                    f"{pos}: api_key_env указывает на «{env_name}», но такая переменная "
                    f"окружения пуста или не задана — добавь секрет с этим именем "
                    f"(Settings → Secrets and variables → Actions)"
                )
        elif _looks_like_placeholder(api_key):
            err(
                f"{pos}: api_key похож на заготовку из .env.example («{api_key[:24]}») — "
                f"замени на настоящий ключ"
            )

        raw_models = entry.get("models")
        single = str(entry.get("model") or "").strip()
        has_models = bool(raw_models) and (
            (isinstance(raw_models, list) and any(str(m).strip() for m in raw_models))
            or (isinstance(raw_models, str) and raw_models.strip())
        )
        if not single and not has_models:
            err(f"{pos}: не указана модель — добавь \"model\": \"имя-модели\" (или список \"models\")")
        elif raw_models is not None and not isinstance(raw_models, (list, str)):
            err(f"{pos}: models должен быть массивом строк или строкой через запятую")

        # --- числовые параметры
        for field_name, low, high in (
            ("max_batch_size", 1, 200),
            ("rpm_delay_sec", 0.0, 600.0),
            ("timeout_sec", 1.0, 600.0),
        ):
            if field_name not in entry:
                continue
            try:
                value = float(entry[field_name])
            except (TypeError, ValueError):
                err(f"{pos}: {field_name} должен быть числом (сейчас «{entry[field_name]}»)")
                continue
            if not (low <= value <= high):
                err(f"{pos}: {field_name}={value:g} вне диапазона {low:g}..{high:g}")

        json_mode = str(entry.get("json_mode") or "json_object").strip().lower()
        if json_mode not in JSON_MODES:
            err(
                f"{pos}: json_mode «{json_mode}» не поддерживается, допустимо: "
                f"{', '.join(JSON_MODES)} (json_schema не реализован — используй json_object)"
            )

        # --- дубли
        if name:
            seen_names[name] = seen_names.get(name, 0) + 1
            if seen_names[name] == 2:
                warn(
                    f"{pos}: имя «{name}» уже встречалось выше. Это допустимо (несколько ключей "
                    f"одного вендора), но тогда дай им разные имена — иначе в логах и статистике "
                    f"их будет не различить"
                )
        if api_key:
            seen_keys[api_key] = seen_keys.get(api_key, 0) + 1
            if seen_keys[api_key] == 2:
                warn(
                    f"{pos}: такой api_key уже используется другим провайдером — "
                    f"один ключ не даст дополнительной квоты; дублируй только если хочешь "
                    f"разные модели/лимиты на одном ключе"
                )
        if env_name:
            seen_keys[f"env:{env_name}"] = seen_keys.get(f"env:{env_name}", 0) + 1
            if seen_keys[f"env:{env_name}"] == 2:
                warn(
                    f"{pos}: api_key_env «{env_name}» уже указан у другого провайдера — "
                    f"это один и тот же ключ, дополнительной квоты не будет"
                )

    return problems


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
    """Классифицирует ошибку.

    auth | rate | overload | context | model_gone | json_unsupported | bad_json | other
    """
    if isinstance(exc, ValueError):
        return "bad_json"
    if isinstance(exc, openai.AuthenticationError):
        return "auth"
    if isinstance(exc, openai.PermissionDeniedError):
        return "auth"
    if isinstance(exc, openai.RateLimitError):
        return "rate"
    if isinstance(exc, openai.NotFoundError):
        # Модель выпилена/переименована у провайдера — ключ при этом жив.
        return "model_gone"
    if isinstance(exc, openai.BadRequestError):
        msg = str(exc).lower()
        # Провайдер не умеет response_format / json_object — надо просто выключить
        # JSON-режим и повторить тот же запрос, это не ошибка ключа и не ошибка модели.
        if "response_format" in msg or "json_object" in msg or "json mode" in msg:
            return "json_unsupported"
        if "context" in msg or "too long" in msg or "maximum" in msg:
            return "context"
        # 400 вида "model not found" / "unsupported model" / "decommissioned"
        if "model" in msg and ("not found" in msg or "unsupported" in msg or "does not exist" in msg or "decommission" in msg):
            return "model_gone"
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
    # Сколько живёт «ключ выключен» в config.json. Раз в сутки перепроверяем:
    # ключ могли починить/оплатить, а вечная блокировка — тоже плохо.
    DISABLED_TTL_HOURS = 24.0

    def __init__(self, providers: list[Provider], disabled: dict[str, dict] | None = None) -> None:
        self.providers = [p for p in providers if p.usable]
        if not self.providers:
            raise ValueError("no usable LLM providers configured")

        # Имена обязаны быть уникальны: по имени хранятся состояние (кулдаун,
        # счётчики, dynamic_cap) и OpenAI-клиент с api_key. Иначе несколько
        # ключей одного провайдера делили бы один кулдаун и один ключ.
        used: set[str] = set()
        for p in self.providers:
            if p.name in used:
                base, i = p.name, 2
                while f"{base}#{i}" in used:
                    i += 1
                p.name = f"{base}#{i}"
                log.warning("duplicate provider name %r renamed to %r", base, p.name)
            used.add(p.name)

        self.states: dict[str, ProviderState] = {p.name: ProviderState() for p in self.providers}
        self._clients: dict[str, OpenAI] = {}
        self._restore_disabled(disabled or {})

    # ------------------------------------------------------- persisted state

    def _restore_disabled(self, saved: dict[str, dict]) -> None:
        """Возвращает выключенные в прошлых прогонах ключи (если TTL не истёк)."""
        if not saved:
            return
        now = datetime.now(timezone.utc)
        for p in self.providers:
            entry = saved.get(p.state_key)
            if not isinstance(entry, dict):
                continue

            until = _parse_iso(entry.get("until"))
            if until is not None and until <= now:
                log.info(
                    "provider %s: срок блокировки истёк (%s) — пробуем снова",
                    p.name,
                    entry.get("until"),
                )
                continue

            st = self.states[p.name]
            st.disabled = True
            st.disabled_reason = str(entry.get("reason") or "disabled in a previous run")
            log.warning(
                "provider %s: выключен с прошлого прогона (%s, до %s) — пропускаем. "
                "Причина: %s",
                p.name,
                entry.get("at") or "?",
                entry.get("until") or "?",
                st.disabled_reason,
            )

            # Модель, на которой провайдер остановился, тоже переживает прогон:
            # иначе каждый запуск снова бьётся в выпиленную модель.
            saved_model = str(entry.get("model") or "").strip()
            models = p.model_list
            if saved_model and saved_model in models:
                st.model_idx = models.index(saved_model)

    def disabled_snapshot(self) -> list[dict]:
        """Что записать в config.json: только реально выключенные, без секретов."""
        now = datetime.now(timezone.utc)
        until = _iso_after(self.DISABLED_TTL_HOURS)
        out: list[dict] = []
        for p in self.providers:
            st = self.states[p.name]
            if not st.disabled:
                continue
            out.append(
                {
                    "key": p.state_key,
                    "name": p.original_name,
                    "key_fp": p.key_fp,
                    "model": self._current_model(p),
                    "reason": (st.disabled_reason or "disabled")[:300],
                    "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "until": until,
                }
            )
        return out

    # ---------------------------------------------------------------- public

    def stats(self) -> dict[str, dict[str, int]]:
        return {name: {"ok": st.ok, "err": st.err} for name, st in self.states.items()}

    def disabled_names(self) -> list[str]:
        return [p.name for p in self.providers if self.states[p.name].disabled]

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
            st.disabled_reason = "authentication failed (401/403): ключ недействителен, отозван или без доступа к модели"
        elif kind == "rate":
            st.cooldown_until = time.monotonic() + self.COOLDOWN_RATE
        elif kind == "overload":
            st.cooldown_until = time.monotonic() + self.COOLDOWN_OVERLOAD
        elif kind == "context":
            # провайдер жив — просто батч слишком большой, cap ужимается отдельно
            pass
        elif kind == "json_unsupported":
            # Обрабатывается внутри _raw_completion (повтор без response_format),
            # сюда попадает только если и повтор не удался.
            st.cooldown_until = time.monotonic() + 5.0
        elif kind == "model_gone":
            # Ключ жив, модель — нет. Переключаемся на следующую модель этого же
            # провайдера; если модели кончились — провайдер выбывает до конца прогона.
            if st.model_idx + 1 < len(provider.model_list):
                st.model_idx += 1
                log.warning(
                    "provider %s: model %r unavailable, switching to %r",
                    provider.name,
                    provider.model_list[st.model_idx - 1],
                    provider.model_list[st.model_idx],
                )
            else:
                st.disabled = True
                st.disabled_reason = (
                    f"все модели недоступны у провайдера ({', '.join(provider.model_list)})"
                )
                log.error(
                    "provider %s: all models exhausted (%s), provider disabled",
                    provider.name,
                    ", ".join(provider.model_list),
                )
        elif kind == "bad_json":
            st.cooldown_until = time.monotonic() + 5.0
        else:
            st.cooldown_until = time.monotonic() + 10.0

    def _current_model(self, provider: Provider) -> str:
        models = provider.model_list
        if not models:
            return provider.model
        idx = min(self.states[provider.name].model_idx, len(models) - 1)
        return models[idx]

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

        def _call() -> str:
            kwargs: dict[str, Any] = {
                "model": self._current_model(provider),
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

        try:
            return _call()
        except Exception as e:  # noqa: BLE001
            if _classify_error(e) != "json_unsupported" or provider.json_mode == "none":
                raise
            # Провайдер не умеет response_format. Это не его вина и не повод
            # тратить попытку failover: выключаем JSON-режим у этого провайдера
            # на весь прогон и повторяем ТОТ ЖЕ запрос сразу.
            log.warning(
                "provider %s: json_object не поддерживается (%s) — переключаюсь на "
                "regex-парсинг JSON и повторяю запрос",
                provider.name,
                str(e)[:160],
            )
            provider.json_mode = "none"
            self._throttle(provider)
            return _call()

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


PROVIDERS_FILE = "providers.json"


def load_providers_raw(raw_json: str, providers_file: str | Path | None = None) -> tuple[str, str]:
    """Возвращает (сырой конфиг, откуда он взялся).

    Приоритет:
      1. LLM_PROVIDERS_JSON из окружения — если задан и непустой (старый способ);
      2. файл providers.json — обычный файл в репозитории, его правят в веб-интерфейсе
         GitHub как любой текст, а ключи подставляются из переменных по api_key_env.

    Второй способ основной: секреты остаются секретами (по одному короткому
    значению на провайдера), а структура провайдеров живёт в git и ревьюится.
    """
    env_raw = (raw_json or "").strip()
    if env_raw and env_raw not in ("[]", "null"):
        return env_raw, "LLM_PROVIDERS_JSON (env)"

    path = Path(providers_file) if providers_file else Path(__file__).resolve().parent.parent / PROVIDERS_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        # Файла нет — пусть валидатор скажет, что конфигурации нет вообще.
        return "", f"{PROVIDERS_FILE} (не найден: {path})"
    return text, str(path)


def build_router_from_json(
    raw_json: str,
    disabled: dict[str, dict] | None = None,
    providers_file: str | Path | None = None,
) -> LLMRouter:
    """Строит роутер из конфига провайдеров.

    Источник: `LLM_PROVIDERS_JSON` из окружения, иначе файл `providers.json`
    (см. load_providers_raw). Ошибки конфигурации собираются ВСЕ сразу —
    человек должен за один прогон увидеть все проблемы, а не по одной за запуск.

    `disabled` — состояние из config.json (settings.disabled_providers),
    чтобы ключ, умерший от 401, не тратил запрос в следующем прогоне.
    """
    raw, source = load_providers_raw(raw_json, providers_file)
    log.info("LLM config source: %s", source)

    problems = validate_providers_json(raw)

    errors = [p.message for p in problems if p.is_error]
    warnings = [p.message for p in problems if not p.is_error]
    for w in warnings:
        log.warning("LLM config: %s", w)

    if errors:
        raise ProvidersConfigError(errors)

    data = json.loads(raw)
    providers = [Provider.from_dict(d) for d in data if isinstance(d, dict)]

    usable = [p for p in providers if p.usable]
    if not usable:
        raise ProvidersConfigError(
            [
                "ни одного рабочего провайдера — все отфильтрованы "
                "(нужны одновременно base_url, ключ (api_key или api_key_env) и model/models)"
            ]
        )

    try:
        return LLMRouter(providers, disabled=disabled)
    except ValueError as e:
        raise ProvidersConfigError([str(e)]) from e
