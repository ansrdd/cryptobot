"""Hourly paper-trading run. Designed to be invoked by a cron (GitHub Actions).

Each run:
  1. refresh hourly bars (binance history cache)
  2. read equity + positions from Alpaca (source of truth)
  3. load persisted state (risk state, per-position stops, open-trade bookkeeping)
  4. check stops / exits / entries with the same strategy + risk code as backtest
  5. send orders, record fills, update state
  6. regenerate dashboard

State lives in state/ (committed back to the repo by the workflow) so a fresh
runner VM can pick up where the last left off. Alpaca positions are reconciled
against our own book every run; if they disagree, Alpaca wins and we log it.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import alerts, backtest, data
from .config import ROOT, load
from .risk import RiskManager, RiskState
from .strategies import build, regime_series

log = logging.getLogger(__name__)
STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "state.json"
DB_FILE = STATE_DIR / "trades.sqlite"


# ------------------------------------------------------------------ storage
def _db() -> sqlite3.Connection:
    STATE_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_FILE)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS trades(
        id INTEGER PRIMARY KEY, symbol TEXT, strategy TEXT, entry_ts TEXT, exit_ts TEXT,
        entry_price REAL, exit_price REAL, units REAL, pnl REAL, pnl_pct REAL, fees REAL,
        reason TEXT, bars_held INTEGER);
    CREATE TABLE IF NOT EXISTS equity(ts TEXT PRIMARY KEY, equity REAL, bench_price REAL);
    CREATE TABLE IF NOT EXISTS decisions(ts TEXT, symbol TEXT, strategy TEXT, action TEXT, detail TEXT);
    """)
    return con


def _load_state(equity: float) -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"risk": RiskState(peak_equity=equity, day_start_equity=equity).to_dict(),
            "book": {},  # key "SYM|strategy" -> {units, entry_price, entry_ts, stop, trail_mult, high_water, max_hold, bars_held}
            "starting_equity": equity, "started": datetime.now(timezone.utc).isoformat()}


def _save_state(st: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(st, indent=2, default=str))


# ------------------------------------------------------------------- main
def run_once(dry: bool = False) -> int:
    cfg = load()
    now = datetime.now(timezone.utc)
    ts_label = now.strftime("%Y-%m-%d %H:%M UTC")
    con = _db()
    status, detail = "ok", ""
    try:
        # 1. data
        hourly = data.load_hourly(cfg)
        bars = data.load_universe(cfg, hourly)          # strategy timeframe (daily)
        for s, df in hourly.items():
            if data.is_stale(df, "1h"):
                alerts.send(f"⚠️ Stale data for {s}: last bar {df.index[-1] if len(df) else 'none'}", "stale_data", cfg)
                status, detail = "stale", f"{s} stale"
        # 2. broker
        if dry:
            broker = None
            equity = cfg.account.starting_equity
            bpos = {}
        else:
            from .broker import PaperBroker
            broker = PaperBroker(cfg)
            broker.cancel_all()
            equity = broker.equity()
            bpos = broker.positions()

        # 3. state
        first_run = not STATE_FILE.exists()
        st = _load_state(equity)
        if first_run and not dry:
            alerts.send(f"🤖 <b>CryptoBot is online</b>\nPaper account equity ${equity:,.0f}\n"
                        f"Trading {', '.join(cfg.universe.symbols)} on {cfg.universe.timeframe} bars, checking stops hourly. "
                        f"You'll hear from me on every trade and whenever something matters.",
                        "info", cfg)
        risk = RiskManager(cfg, equity, RiskState.from_dict(st["risk"]))
        book: dict = st["book"]
        strategies = build(cfg)
        cost = (cfg.costs.taker_fee_bps + cfg.costs.slippage_bps) / 10_000

        # reconcile: Alpaca is truth. Our book attributes units to strategies.
        # (dry runs have no broker to reconcile against, so the book is trusted as-is)
        for sym in ([] if dry else cfg.universe.symbols):
            have = bpos.get(sym).qty if sym in bpos else 0.0
            ours = sum(v["units"] for k, v in book.items() if k.startswith(sym + "|"))
            if abs(have - ours) > max(1e-6, 0.001 * max(have, ours)):
                log.warning("reconcile %s: broker=%s book=%s — adjusting book", sym, have, ours)
                con.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (ts_label, sym, "*", "reconcile", f"broker={have} book={ours}"))
                if ours > 0:
                    scale = have / ours
                    for k in [k for k in book if k.startswith(sym + "|")]:
                        book[k]["units"] *= scale
                        if book[k]["units"] <= 0:
                            del book[k]
                elif have > 0:
                    book[f"{sym}|manual"] = {"units": have, "entry_price": bpos[sym].avg_entry, "entry_ts": ts_label,
                                             "stop": 0.0, "trail_mult": 0.0, "high_water": bpos[sym].current_price,
                                             "max_hold": None, "bars_held": 0}

        ts = bars[cfg.universe.symbols[0]].index[-1]
        risk.on_bar(ts, equity)
        if risk.state.killed and not st.get("kill_alerted"):
            alerts.send(f"🛑 <b>KILL SWITCH</b>\n{risk.state.kill_reason}\nBot is flat and disabled. Edit state/state.json to re-arm.", "kill_switch", cfg)
            st["kill_alerted"] = True
        if risk.state.halted_for_day and not st.get("halt_alerted_day") == str(risk.state.day):
            alerts.send(f"⏸ Daily loss limit hit ({cfg.risk.daily_loss_limit_pct}%). Flat until tomorrow UTC.", "daily_limit", cfg)
            st["halt_alerted_day"] = str(risk.state.day)

        acted: dict = st.setdefault("acted", {})          # key -> strategy bar we last entered/exited on
        last_checked = pd.Timestamp(st["last_hour_checked"]) if st.get("last_hour_checked") else None
        orders: list[tuple[str, str, str, float, str]] = []  # (sym, strat, side, units, reason)
        gross = sum(v["units"] * bars[k.split("|")[0]]["close"].iloc[-1] for k, v in book.items())

        # 4a. exits / stops on existing book
        for key, p in list(book.items()):
            sym, stname = key.split("|")
            df = bars[sym]
            row = df.iloc[-1]
            if p.get("entry_bar"):
                p["bars_held"] = int((df.index > pd.Timestamp(p["entry_bar"])).sum())
            # stop check on every hourly bar since the last run (not just the daily low),
            # so a crash mid-day is caught within the hour, like the backtest's intrabar stop
            h = hourly[sym]
            recent = h[h.index > last_checked] if last_checked is not None else h.iloc[-1:]
            hour_low = float(recent["low"].min()) if len(recent) else float(h["low"].iloc[-1])
            stobj = next((s for s in strategies if s.name == stname), None)
            sig = stobj.signals(df).iloc[-1] if stobj else None
            if p.get("trail_mult", 0) > 0 and sig is not None:
                p["high_water"] = max(p.get("high_water", row["close"]), row["close"])
                p["stop"] = max(p["stop"], p["high_water"] - p["trail_mult"] * float(sig["atr"]))
            reason = None
            if risk.must_flatten():
                reason = "risk_flatten"
            elif p.get("stop", 0) > 0 and hour_low <= p["stop"]:
                reason = "stop"
            elif sig is not None and bool(sig["exit"]):
                reason = "signal"
            elif p.get("max_hold") and p["bars_held"] >= p["max_hold"]:
                reason = "time_stop"
            if reason:
                orders.append((sym, stname, "sell", p["units"], reason))

        # 4b. entries
        if not risk.must_flatten():
            for sym in cfg.universe.symbols:
                df = bars[sym]
                reg = regime_series(df, cfg).iloc[-1]
                for stobj in strategies:
                    key = f"{sym}|{stobj.name}"
                    if key in book:
                        continue
                    if acted.get(key) == str(df.index[-1]):
                        continue  # already traded this bar (e.g. stopped out today) — wait for a fresh signal
                    sig = stobj.signals(df).iloc[-1]
                    if not bool(sig["entry"]) or np.isnan(sig["stop"]):
                        con.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (ts_label, sym, stobj.name, "hold", "no entry signal"))
                        continue
                    ok, why = risk.can_open(len(book), gross, equity)
                    if not ok:
                        con.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (ts_label, sym, stobj.name, "blocked", why))
                        continue
                    w = dict(cfg.regime.weights)[reg][stobj.name] if cfg.regime.enabled else 1.0
                    px = float(df["close"].iloc[-1])
                    units = risk.size(equity, px, float(sig["stop"]), weight=w, gross_exposure=gross)
                    if units * px < 10:
                        continue
                    gross += units * px
                    orders.append((sym, stobj.name, "buy", units, "signal"))
                    book[key] = {"units": units, "entry_price": px, "entry_ts": ts_label, "entry_bar": str(df.index[-1]),
                                 "stop": float(sig["stop"]),
                                 "trail_mult": float(sig["trail_mult"]), "high_water": px, "bars_held": 0,
                                 "max_hold": int(sig["max_hold"]) if "max_hold" in sig and not np.isnan(sig["max_hold"]) else None,
                                 "pending": True}

        # 5. execute
        for (sym, stname, side, units, reason) in orders:
            key = f"{sym}|{stname}"
            if dry:
                px = float(bars[sym]["close"].iloc[-1])
                res = {"status": "filled", "filled_qty": units, "fill_price": px}
            else:
                res = broker.sell(sym, units) if side == "sell" else broker.buy(sym, units)
            con.execute("INSERT INTO decisions VALUES(?,?,?,?,?)", (ts_label, sym, stname, side, json.dumps({"units": units, "reason": reason, **res})))
            if res["status"] in ("rejected", "unfilled"):
                alerts.send(f"❌ Order {side} {units:.5f} {sym} {res['status']}: {res.get('detail','')}", "order_rejected", cfg)
                if side == "buy":
                    book.pop(key, None)
                continue
            filled, fpx = res.get("filled_qty", units), res.get("fill_price") or float(bars[sym]["close"].iloc[-1])
            acted[key] = str(bars[sym].index[-1])
            if side == "buy":
                book[key].update(units=filled, entry_price=fpx * (1 + cost))  # pessimistic cost mark
                book[key].pop("pending", None)
                alerts.send(alerts.buy_msg(sym), "trade", cfg)
            else:
                p = book.pop(key)
                exit_px = fpx * (1 - cost)
                pnl = filled * (exit_px - p["entry_price"])
                con.execute("INSERT INTO trades(symbol,strategy,entry_ts,exit_ts,entry_price,exit_price,units,pnl,pnl_pct,fees,reason,bars_held) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            (sym, stname, p["entry_ts"], ts_label, p["entry_price"], exit_px, filled, pnl,
                             100 * (exit_px / p["entry_price"] - 1), filled * (p["entry_price"] + exit_px) * cost, reason, p["bars_held"]))
                risk.on_trade_closed(pnl)
                alerts.send(alerts.sell_msg(sym, reason, pnl), "trade", cfg)
                if filled < p["units"] * 0.999:  # partial exit: keep remainder
                    p["units"] -= filled
                    book[key] = p

        # 6. equity snapshot + persist
        bench_px = float(hourly[cfg.backtest.benchmark]["close"].iloc[-1]) if cfg.backtest.benchmark in hourly else float("nan")
        con.execute("INSERT OR REPLACE INTO equity VALUES(?,?,?)", (ts_label, equity, bench_px))
        st["risk"] = risk.state.to_dict()
        st["book"] = book
        st["last_run"] = ts_label
        st["last_hour_checked"] = str(hourly[cfg.universe.symbols[0]].index[-1])
        _save_state(st)
        con.commit()

        # weekly report (Mondays 00:xx UTC)
        if now.weekday() == 0 and now.hour == 0:
            eqs = pd.read_sql("SELECT * FROM equity ORDER BY ts", con)
            if len(eqs) > 24 * 7:
                wk = eqs.equity.iloc[-1] / eqs.equity.iloc[-24 * 7] - 1
                alerts.send(alerts.weekly_msg(wk, equity, len(book)), "weekly_report", cfg)

        write_dashboard(cfg, con, st, hourly, broker, status)
        return 0
    except Exception as e:  # noqa: BLE001
        log.exception("run failed")
        alerts.send(f"💥 Run failed: {e}\n<pre>{traceback.format_exc()[-800:]}</pre>", "run_failed", cfg)
        try:
            st = _load_state(cfg.account.starting_equity)
            write_dashboard(cfg, con, st, None, None, "error", str(e))
        except Exception:  # noqa: BLE001
            pass
        return 1
    finally:
        con.close()


def write_dashboard(cfg, con, st, bars, broker, status="ok", detail=""):
    from .dashboard import render
    eqs = pd.read_sql("SELECT * FROM equity ORDER BY ts", con)
    trades = pd.read_sql("SELECT * FROM trades ORDER BY exit_ts", con)
    eq0 = st.get("starting_equity", cfg.account.starting_equity)
    if len(eqs) < 2:
        idx = pd.DatetimeIndex([pd.Timestamp.utcnow().floor("h") - pd.Timedelta(hours=1), pd.Timestamp.utcnow().floor("h")])
        equity = pd.Series([eq0, eqs.equity.iloc[-1] if len(eqs) else eq0], index=idx)
        bench = pd.Series([eq0, eq0], index=idx)
    else:
        idx = pd.to_datetime(eqs.ts.str.replace(" UTC", ""), utc=True)
        equity = pd.Series(eqs.equity.values, index=idx)
        bp = eqs.bench_price.ffill().bfill()
        bench = pd.Series((eq0 * bp / bp.iloc[0]).values, index=idx)
    res = backtest.Result(equity, [], bench)
    res.trades = [backtest.Trade(**{k: r[k] for k in backtest.Trade.__dataclass_fields__}) for _, r in trades.iterrows()] if len(trades) else []
    m = backtest.metrics(res) if len(equity) > 2 else {"benchmark_return_pct": 0.0, "max_drawdown_pct": 0.0, "n_trades": len(trades)}
    # open positions with live marks
    opens = []
    for k, p in st.get("book", {}).items():
        sym, stname = k.split("|")
        cur = float(bars[sym]["close"].iloc[-1]) if bars and sym in bars else p["entry_price"]
        opens.append({"symbol": sym, "strategy": stname, "units": p["units"], "entry_price": p["entry_price"],
                      "current_price": cur, "stop": p.get("stop", 0.0), "unrealized_pnl": p["units"] * (cur - p["entry_price"])})
    rs = st.get("risk", {})
    if status == "ok" and rs.get("killed"):
        status = "killed"
    elif status == "ok" and rs.get("halted_for_day"):
        status = "halted"
    meta = {"mode": cfg.mode, "starting_equity": eq0, "last_run": st.get("last_run"), "status": status,
            "open_positions": opens, "benchmark": cfg.backtest.benchmark, "kill_pct": cfg.risk.max_drawdown_kill_pct,
            "fee_bps": cfg.costs.taker_fee_bps, "slip_bps": cfg.costs.slippage_bps,
            "notes": detail or f"Paper trading since {st.get('started','')[:10]}. Judge after 90 days, not 9."}
    out = ROOT / cfg.dashboard.output
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(equity, bench, res.trades_df, m, meta, cfg.dashboard.title))
    log.info("dashboard -> %s", out)
