"""Alpaca paper broker adapter. Alpaca is the source of truth for what we hold.

Keys come from env vars (GitHub Secrets):  ALPACA_API_KEY, ALPACA_SECRET_KEY
Paper only: the client is always constructed with paper=True and refuses otherwise.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class BrokerPosition:
    symbol: str      # "BTC/USD"
    qty: float
    avg_entry: float
    market_value: float
    current_price: float


class PaperBroker:
    def __init__(self, cfg):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import CryptoHistoricalDataClient
        key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
        if not key or not sec:
            raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
        if cfg.mode != "paper":
            raise RuntimeError("PaperBroker only runs in paper mode")
        self.cfg = cfg
        self.tc = TradingClient(key, sec, paper=True)
        self.dc = CryptoHistoricalDataClient(key, sec)
        acct = self.tc.get_account()
        log.info("paper account %s · equity %s", getattr(acct, "account_number", "?"), acct.equity)

    # ------------------------------------------------------------- account
    def equity(self) -> float:
        return float(self.tc.get_account().equity)

    def cash(self) -> float:
        return float(self.tc.get_account().cash)

    def positions(self) -> dict[str, BrokerPosition]:
        out = {}
        for p in self.tc.get_all_positions():
            sym = p.symbol  # Alpaca returns "BTCUSD"
            norm = sym if "/" in sym else f"{sym[:-3]}/{sym[-3:]}"
            out[norm] = BrokerPosition(norm, float(p.qty), float(p.avg_entry_price),
                                       float(p.market_value), float(p.current_price))
        return out

    def latest_price(self, sym: str) -> float:
        from alpaca.data.requests import CryptoLatestQuoteRequest
        q = self.dc.get_crypto_latest_quote(CryptoLatestQuoteRequest(symbol_or_symbols=sym))[sym]
        return (float(q.ask_price) + float(q.bid_price)) / 2

    # -------------------------------------------------------------- orders
    def cancel_all(self) -> None:
        try:
            self.tc.cancel_orders()
        except Exception as e:  # noqa: BLE001
            log.warning("cancel_all: %s", e)

    def buy(self, sym: str, qty: float) -> dict:
        return self._order(sym, qty, "buy")

    def sell(self, sym: str, qty: float) -> dict:
        return self._order(sym, qty, "sell")

    def _order(self, sym: str, qty: float, side: str) -> dict:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest
        qty = round(qty, 6)
        if qty <= 0:
            return {"status": "skipped", "reason": "qty<=0"}
        ex = self.cfg.execution
        s = OrderSide.BUY if side == "buy" else OrderSide.SELL
        if ex.order_type == "limit":
            px = self.latest_price(sym)
            off = ex.limit_offset_bps / 10_000
            limit = px * (1 + off) if side == "buy" else px * (1 - off)
            req = LimitOrderRequest(symbol=sym, qty=qty, side=s, time_in_force=TimeInForce.GTC,
                                    limit_price=round(limit, 2))
        else:
            req = MarketOrderRequest(symbol=sym, qty=qty, side=s, time_in_force=TimeInForce.GTC)
        o = self.tc.submit_order(req)
        log.info("submitted %s %s %s id=%s", side, qty, sym, o.id)
        # wait for fill up to timeout, then cancel remainder
        deadline = time.time() + ex.limit_timeout_sec
        while time.time() < deadline:
            o = self.tc.get_order_by_id(o.id)
            if str(o.status) in ("OrderStatus.FILLED", "filled"):
                break
            if str(o.status) in ("OrderStatus.REJECTED", "rejected", "OrderStatus.CANCELED", "canceled"):
                return {"status": "rejected", "id": str(o.id), "detail": str(o.status)}
            time.sleep(5)
        filled = float(o.filled_qty or 0)
        if filled < qty:
            try:
                self.tc.cancel_order_by_id(o.id)
            except Exception:  # noqa: BLE001
                pass
        return {"status": "filled" if filled >= qty * 0.999 else "partial" if filled > 0 else "unfilled",
                "id": str(o.id), "filled_qty": filled,
                "fill_price": float(o.filled_avg_price) if o.filled_avg_price else None}
