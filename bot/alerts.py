"""Telegram alerts. Silent if not configured. Never raises."""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)


def send(text: str, kind: str = "info", cfg=None) -> bool:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if cfg is not None and (not cfg.alerts.telegram or (kind not in cfg.alerts.events and kind != "info")):
        return False
    if not tok or not chat:
        log.info("[alert:%s] %s", kind, text)
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": text, "parse_mode": "HTML"}, timeout=15)
        if r.ok:
            log.info("telegram sent (%s)", kind)
        else:
            log.warning("telegram rejected (%s): HTTP %s %s", kind, r.status_code, r.text[:200])
        return r.ok
    except Exception as e:  # noqa: BLE001
        log.warning("telegram failed: %s", e)
        return False
