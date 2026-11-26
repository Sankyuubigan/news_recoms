"""Проверка LLM_PROVIDERS_JSON без запуска пайплайна.

    python check_llm.py                 # берёт LLM_PROVIDERS_JSON из env/.env
    python check_llm.py providers.json  # из файла
    echo "$JSON" | python check_llm.py -   # из stdin

Коды возврата: 0 — всё ок (возможны предупреждения), 1 — есть ошибки.
Секреты не печатаются: только отпечатки ключей.
"""
from __future__ import annotations

import json
import sys

from config import get_settings
from llm.router import (
    ProvidersConfigError,
    build_router_from_json,
    key_fingerprint,
    validate_providers_json,
)


def _read_raw(argv: list[str]) -> str:
    if not argv or argv[0] == "-":
        if argv:
            return sys.stdin.read()
        return get_settings().llm_providers_json
    with open(argv[0], "r", encoding="utf-8") as fh:
        return fh.read()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        raw = _read_raw(argv)
    except OSError as e:
        print(f"не удалось прочитать конфиг: {e}", file=sys.stderr)
        return 1

    problems = validate_providers_json(raw)
    errors = [p for p in problems if p.is_error]
    warnings = [p for p in problems if not p.is_error]

    for p in warnings:
        print(f"ПРЕДУПРЕЖДЕНИЕ: {p.message}")
    for p in errors:
        print(f"ОШИБКА: {p.message}")

    if errors:
        print(f"\nИтог: {len(errors)} ошибк(и) — пайплайн не сможет работать с ИИ.")
        return 1

    # Конфиг валиден — показываем, что из него получилось
    try:
        router = build_router_from_json(raw)
    except ProvidersConfigError as e:
        for p in e.problems:
            print(f"ОШИБКА: {p}")
        return 1

    print(f"\nИтог: конфиг валиден, провайдеров: {len(router.providers)}")
    print(f"{'#':<3} {'имя':<26} {'ключ':<14} {'модели':<44} батч")
    for i, p in enumerate(router.providers, 1):
        models = ", ".join(p.model_list)
        print(f"{i:<3} {p.name[:26]:<26} {p.key_fp:<14} {models[:44]:<44} {p.max_batch_size}")
        print(f"    {p.base_url}  json_mode={p.json_mode}  timeout={p.timeout_sec:g}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
