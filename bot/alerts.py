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


# ---------------------------------------------------------------- message wording (Andres's words)
SELL_REASONS = {
    "stop": "it hit the stop loss",
    "signal": "the trend flipped",
    "time_stop": "we held it too long",
    "risk_flatten": "the risk limits said get out",
}


def _coin(sym: str) -> str:
    return sym.split("/")[0]  # "BTC/USD" -> "BTC"


def buy_msg(sym: str) -> str:
    return f"We're buying {_coin(sym)} rn"


def sell_msg(sym: str, reason: str, pnl: float) -> str:
    msg = f"Just sold {_coin(sym)} bc {SELL_REASONS.get(reason, reason)}"
    if pnl > 0:
        msg += f"\nWe just made ${pnl:,.2f} bro"
    return msg


def weekly_msg(week_ret: float, equity: float, n_open: int) -> str:
    if week_ret >= 0.02:
        mood = "Looking good, solid week"
    elif week_ret >= 0:
        mood = "Looking good, up a little"
    elif week_ret > -0.02:
        mood = "Down a little, nothing crazy"
    else:
        mood = "Rough week, we're down"
    return (f"This week: {week_ret * 100:+.2f}%\nEquity: ${equity:,.0f}\n"
            f"Open positions: {n_open}\n{mood}")
