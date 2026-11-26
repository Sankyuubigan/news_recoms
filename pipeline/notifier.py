from __future__ import annotations

import html
import logging

import httpx

log = logging.getLogger(__name__)


class TelegramNotifier:
    """Слой 4: алерты в Telegram для постов с оценкой >= порога."""

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 20.0) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_alert(self, alert: dict) -> bool:
        if not self.enabled:
            return False

        title = alert.get("title_ru") or "Без заголовка"
        score = alert.get("ai_score")
        summary = alert.get("summary_ru") or ""
        url = alert.get("url") or ""

        score_str = f"{float(score):.1f}/10" if score is not None else "—"

        text = (
            f"🔥 <b>{html.escape(title)}</b>\n\n"
            f"⭐ Оценка: <b>{score_str}</b>\n\n"
            f"{html.escape(summary[:900])}\n\n"
            f'<a href="{html.escape(url)}">Читать оригинал</a>'
        )

        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.post(
                    f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
                    json={
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": False,
                    },
                )
                res.raise_for_status()
                return True
        except Exception as e:  # noqa: BLE001 — алерты не должны ронять пайплайн
            log.warning("telegram alert failed: %s", str(e)[:200])
            return False

    def send_alerts(self, alerts: list[dict]) -> int:
        sent = 0
        for a in alerts:
            if self.send_alert(a):
                sent += 1
        return sent
