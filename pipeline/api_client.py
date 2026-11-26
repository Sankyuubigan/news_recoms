from __future__ import annotations

import json
from typing import Any

import httpx

from models import Bootstrap, IngestItem, RunStats


class WorkerClient:
    """HTTP-клиент к Cloudflare Worker API (/api/internal/*)."""

    def __init__(self, base_url: str, secret: str, timeout: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
            "User-Agent": "NewsRecoms-Pipeline/1.0",
        }
        self._timeout = timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self._base, headers=self._headers, timeout=self._timeout)

    def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        with self._client() as client:
            res = client.request(method, path, **kwargs)
            res.raise_for_status()
            return res.json()

    # ------------------------------------------------------------ endpoints

    def bootstrap(self) -> Bootstrap:
        return Bootstrap.model_validate(self._request("GET", "/api/internal/bootstrap"))

    def ingest(self, items: list[IngestItem]) -> dict:
        payload = {"items": [it.model_dump(mode="json") for it in items]}
        return self._request("POST", "/api/internal/ingest", json=payload)

    def update_source_status(
        self, updates: list[dict[str, Any]]
    ) -> dict:
        return self._request("PATCH", "/api/internal/sources/status", json={"updates": updates})

    def unprocessed_feedback(self) -> list[dict]:
        data = self._request("GET", "/api/internal/feedback/unprocessed")
        return data.get("feedback", []) or []

    def update_profile(self, learned_profile: str, item_ids: list[int]) -> dict:
        return self._request(
            "POST",
            "/api/internal/profile/update",
            json={"learned_profile": learned_profile, "item_ids": item_ids},
        )

    def record_run(self, stats: RunStats) -> dict:
        payload = {
            "started_at": stats.started_at,
            "fetched_count": stats.fetched_count,
            "ad_blocked_count": stats.ad_blocked_count,
            "dedup_skipped_count": stats.dedup_skipped_count,
            "ai_processed_count": stats.ai_processed_count,
            "inbox_added_count": stats.inbox_added_count,
            "rejected_count": stats.rejected_count,
            "pending_ai_count": stats.pending_ai_count,
            "provider_stats": stats.provider_stats,
            "errors": stats.errors,
            "status": stats.status,
        }
        return self._request("POST", "/api/internal/runs", json=payload)


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)
