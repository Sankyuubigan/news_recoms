"""Зонд квот Gemini: показывает, ЧТО именно мы выжигаем.

Запускается только на GitHub Actions (ключи живут в секретах репо),
вручную через workflow_dispatch. В прод-пайплайне не участвует.

Зачем: документация Google не публикует лимиты free-tier — они видны
только в консоли конкретного аккаунта. Поэтому единственный честный
способ узнать, что упирается (RPD / RPM / TPM) — сделать запросы и
прочитать тело 429, где Google сам называет метрику:

    quotaMetric: "generate_content_free_tier_requests"
    quotaId:     "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    quotaValue:  "250"
    retryDelay:  "37s"

Из `quotaId` сразу видно: PerDay (суточная) / PerMinute (минутная) /
InputTokensPerMinute (TPM). От этого зависит стратегия:
    PerDay    → ключ бесполезен до полуночи PT, надо другое ведро;
    PerMinute → короткий кулдаун, тот же ключ годится;
    Tokens    → ужимать батч, а не ждать.
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

BASE = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"

MODELS = ["gemini-3.5-flash", "gemini-3.5-flash-lite"]
KEYS = ["GEMINI_KEY_1", "GEMINI_KEY_2"]

# Крошечный промпт: чтобы TPM заведомо не был узким местом и мы мерили
# именно квоту ЗАПРОСОВ, а не токенов.
SYSTEM = "Отвечай строго JSON."
USER = (
    'Оцени пост. Верни JSON: {"results":[{"temp_id":"p1","is_ad":false,'
    '"score":7.0,"category":"ИИ","tags":["тест"],"title_ru":"Тест",'
    '"summary_ru":"Тестовая выжимка.","key_points_ru":["пункт"],'
    '"ai_reason":"тест","cluster_slug":null}]}'
)


def probe(key_name: str, key: str, model: str, attempts: int = 4) -> dict:
    """Делает несколько запросов и возвращает сводку по отказам."""
    out = {
        "key": key_name,
        "key_prefix": (key[:6] + "…") if key else "(нет)",
        "model": model,
        "ok": 0,
        "codes": [],
        "quota_metric": None,
        "quota_id": None,
        "quota_value": None,
        "retry_delay": None,
        "message": None,
        "headers": {},
    }
    if not key:
        out["message"] = "ключ не задан в секретах"
        return out

    for i in range(attempts):
        try:
            r = httpx.post(
                BASE,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": USER},
                    ],
                    "temperature": 0,
                    "max_tokens": 200,
                },
                timeout=60.0,
            )
        except Exception as e:  # noqa: BLE001
            out["codes"].append(f"EXC:{type(e).__name__}")
            out["message"] = str(e)[:200]
            break

        out["codes"].append(r.status_code)
        # Заголовки лимитов — только с первого ответа, они одинаковые.
        if not out["headers"]:
            out["headers"] = {
                k: v
                for k, v in r.headers.items()
                if "ratelimit" in k.lower() or "retry" in k.lower() or "quota" in k.lower()
            }

        if r.status_code == 200:
            out["ok"] += 1
            continue

        # Разбираем тело ошибки — там Google сам называет метрику.
        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            out["message"] = r.text[:300]
            break

        err = body.get("error") or body
        details = err.get("details") or []
        for d in details:
            # google.rpc.QuotaFailure
            for v in d.get("violations") or []:
                out["quota_metric"] = v.get("quotaMetric") or out["quota_metric"]
                out["quota_id"] = v.get("quotaId") or out["quota_id"]
                out["quota_value"] = v.get("quotaValue") or out["quota_value"]
            # google.rpc.RetryInfo
            if d.get("retryDelay"):
                out["retry_delay"] = d["retryDelay"]
        out["message"] = (err.get("message") or "")[:200]

        # 429 по квоте дальше долбить бессмысленно — только жжём.
        if r.status_code == 429 and out["quota_id"]:
            break
        # 503 перегрузка — подождём и попробуем ещё, чтобы понять, стабильно ли.
        if r.status_code == 503:
            time.sleep(3)
            continue
        break

    return out


def main() -> int:
    rows = []
    for key_name in KEYS:
        key = os.environ.get(key_name, "").strip()
        for model in MODELS:
            row = probe(key_name, key, model)
            rows.append(row)
            print(
                f"{row['key']:<14} {row['model']:<24} "
                f"ok={row['ok']} codes={row['codes']} "
                f"quota_id={row['quota_id']} value={row['quota_value']}"
            )
            if row["retry_delay"]:
                print(f"{'':<14} retryDelay={row['retry_delay']}")
            if row["message"]:
                print(f"{'':<14} msg={row['message']}")
            # Между парами — пауза, чтобы не ловить минутный лимит на ровном месте.
            time.sleep(2)

    print("\n=== HEADERS ===")
    for row in rows:
        if row["headers"]:
            print(f"{row['key']} / {row['model']}: {json.dumps(row['headers'], indent=2)}")

    print("\n=== SUMMARY ===")
    alive = [r for r in rows if r["ok"] > 0]
    dead = [r for r in rows if r["ok"] == 0]
    print(f"рабочих пар (ключ×модель): {len(alive)} из {len(rows)}")
    for r in dead:
        print(f"  МЁРТВАЯ: {r['key']} × {r['model']} — {r['quota_id'] or r['codes']}")
    for r in alive:
        print(f"  ЖИВАЯ:   {r['key']} × {r['model']} — ok={r['ok']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
