"""Проверка конфига LLM-провайдеров без запуска пайплайна.

    python check_llm.py                 # providers.json (или LLM_PROVIDERS_JSON)
    python check_llm.py providers.json  # из конкретного файла
    echo "$JSON" | python check_llm.py -   # из stdin

Коды возврата: 0 — всё ок (возможны предупреждения), 1 — есть ошибки.
Секреты не печатаются: только отпечатки ключей.
"""
from __future__ import annotations

import sys

from config import get_settings
from llm.router import (
    ProvidersConfigError,
    build_router_from_json,
    load_providers_raw,
    validate_providers_json,
)


def _read_raw(argv: list[str]) -> tuple[str, str]:
    if not argv:
        # Штатный путь: env LLM_PROVIDERS_JSON → pipeline/providers.json
        return load_providers_raw(get_settings().llm_providers_json)
    if argv[0] == "-":
        return sys.stdin.read(), "stdin"
    with open(argv[0], "r", encoding="utf-8") as fh:
        return fh.read(), argv[0]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        raw, source = _read_raw(argv)
    except OSError as e:
        print(f"не удалось прочитать конфиг: {e}", file=sys.stderr)
        return 1

    print(f"Источник конфига: {source}\n")

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
    print(f"{'#':<3} {'имя':<26} {'ключ':<14} {'модели':<46} батч")
    for i, p in enumerate(router.providers, 1):
        models = ", ".join(p.model_list)
        print(f"{i:<3} {p.name[:26]:<26} {p.key_fp:<14} {models[:46]:<46} {p.max_batch_size}")
        print(f"    {p.base_url}  json_mode={p.json_mode}  timeout={p.timeout_sec:g}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
