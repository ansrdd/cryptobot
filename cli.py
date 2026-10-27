#!/usr/bin/env python3
"""CryptoBot command line.

  python cli.py backtest            # full history backtest + walk-forward → docs/backtest.html
  python cli.py backtest --synthetic  # same, on fake data (offline smoke test)
  python cli.py run                 # one paper-trading cycle (what the cron calls)
  python cli.py run --dry           # cycle without touching Alpaca (uses config equity)
  python cli.py fetch               # just refresh the data cache
  python cli.py daytrade-backtest   # QuantFlow clone on 5m bars → docs/daytrade_backtest.md
  python cli.py daytrade-backtest --synthetic  # same, on fake data (offline smoke test)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd  # noqa: E402

from bot import backtest, data  # noqa: E402
from bot.config import ROOT, load  # noqa: E402


def cmd_backtest(args):
    cfg = load()
    if args.synthetic:
        bars = {s: data.resample(data.synthetic(s, seed=i * 13 + 7, start_price=30000 if "BTC" in s else 2000),
                                 cfg.universe.timeframe)
                for i, s in enumerate(cfg.universe.symbols)}
        note = "SYNTHETIC DATA — smoke test only. No conclusions."
    else:
        bars = data.load_universe(cfg)
        note = f"Historical backtest on real {cfg.data.source} hourly data from {cfg.data.history_start}."
    res = backtest.run(cfg, bars)
    m = backtest.metrics(res)
    wf = backtest.walk_forward(cfg, bars)
    print("\n=== BACKTEST ===")
    for k, v in m.items():
        print(f"  {k:28s} {v:.3f}" if isinstance(v, float) else f"  {k:28s} {v}")
    print("\n=== WALK-FORWARD (out-of-sample windows) ===")
    print(wf.round(2).to_string(index=False) if len(wf) else "  (not enough history)")
    if res.killed_at is not None:
        print(f"\n  ⚠ kill switch fired at {res.killed_at}: {res.kill_reason}")

    from bot.dashboard import render
    meta = {"mode": "backtest", "starting_equity": cfg.account.starting_equity, "status": "ok",
            "benchmark": cfg.backtest.benchmark, "kill_pct": cfg.risk.max_drawdown_kill_pct,
            "fee_bps": cfg.costs.taker_fee_bps, "slip_bps": cfg.costs.slippage_bps,
            "walk_forward": wf, "notes": note,
            "last_run": f"{res.equity.index[0]:%Y-%m-%d} → {res.equity.index[-1]:%Y-%m-%d}"}
    out = ROOT / "docs" / ("backtest_synthetic.html" if args.synthetic else "backtest.html")
    out.parent.mkdir(exist_ok=True)
    out.write_text(render(res.equity, res.benchmark, res.trades_df, m, meta,
                          title="CryptoBot — Backtest" + (" (synthetic)" if args.synthetic else "")))
    print(f"\n  dashboard → {out}")
    return 0


def cmd_daytrade_backtest(args):
    """Locked QuantFlow clone test (docs/DAYTRADE_SPEC.md). One verdict, at real costs."""
    from bot import daytrade
    cfg = daytrade.load_config()
    u = cfg["universe"]
    if args.synthetic:
        n = 12 * 24 * 180  # 180 days of 5m bars
        bars = {}
        for i, s in enumerate(u["symbols"]):
            df = data.synthetic(s, bars=n, seed=i * 13 + 7, start_price=30000 if "BTC" in s else 2000)
            df.index = pd.date_range(end=df.index[-1], periods=n, freq="5min", tz="UTC")
            bars[s] = df
        note = "SYNTHETIC DATA: smoke test only. No conclusions."
    else:
        bars = {s: data.update(ROOT / "data", s, u["timeframe"], u["history_start"], "coinbase")
                for s in u["symbols"]}
        note = f"Coinbase {u['timeframe']} candles from {u['history_start']}."
    eq0 = cfg["account"]["starting_equity"]

    res = daytrade.run(cfg, bars)
    m = daytrade.metrics(res, eq0)
    checks = daytrade.verdict(m, cfg["passing_bar"])
    passed = all(checks.values())

    diag = {}
    for name, c in cfg["costs"]["diagnostics"].items():
        r = daytrade.run(cfg, bars, fee_bps=c["fee_bps"], slippage_bps=c["slippage_bps"])
        diag[name] = (c, daytrade.metrics(r, eq0))
    tdf = res.trades_df

    def fmt(v):
        return f"{v:,.2f}" if isinstance(v, float) else str(v)

    lines = [f"# Day trading backtest: QuantFlow clone ({'synthetic' if args.synthetic else 'real data'})", "",
             f"{note} Spec: docs/DAYTRADE_SPEC.md. Settings: config_daytrade.yaml.", "",
             f"Period: {res.equity.index[0]:%Y-%m-%d %H:%M} to {res.equity.index[-1]:%Y-%m-%d %H:%M} UTC, "
             f"{', '.join(f'{s} {len(b):,} bars' for s, b in bars.items())}", "",
             f"## Verdict at real costs ({cfg['costs']['fee_bps']} bps fee + {cfg['costs']['slippage_bps']} bps slippage per side): "
             f"**{'PASS' if passed else 'FAIL'}**", "",
             "| Check | Result | |", "|---|---|---|"]
    vals = [m["sharpe"], m["profit_factor"], m["total_return_pct"], m["n_trades"], m["pct_quarters_profitable"]]
    for (k, ok), v in zip(checks.items(), vals):
        lines.append(f"| {k} | {fmt(v)} | {'pass' if ok else 'FAIL'} |")
    lines += ["", f"Max drawdown {m['max_drawdown_pct']:.1f}%, win rate {m['win_rate_pct']:.1f}%, "
              f"daily loss halts {m['daily_halts']}, kill switch (20% DD, recorded only): "
              f"{m['kill_switch_at'] or 'never'}", "",
              "## Diagnostics (NOT the verdict)", "", "Cost sensitivity:", "",
              "| Costs | Return % | Sharpe | PF | Trades |", "|---|---|---|---|---|",
              f"| real ({cfg['costs']['fee_bps']}+{cfg['costs']['slippage_bps']} bps) | {m['total_return_pct']:.2f} | "
              f"{m['sharpe']:.2f} | {m['profit_factor']:.2f} | {m['n_trades']} |"]
    for name, (c, dm) in diag.items():
        lines.append(f"| {name} ({c['fee_bps']}+{c['slippage_bps']} bps) | {dm['total_return_pct']:.2f} | "
                     f"{dm['sharpe']:.2f} | {dm['profit_factor']:.2f} | {dm['n_trades']} |")
    lines += ["", "Per signal source and per coin, at real costs. Picking the best row after seeing this is "
              "curve fitting; any follow up needs its own locked spec and a fresh test period.", ""]
    for by in ("source", "symbol", "reason"):
        b = daytrade.breakdown(tdf, by)
        if len(b):
            lines += [b.round(2).to_markdown() if _has_tabulate() else "```\n" + b.round(2).to_string() + "\n```", ""]
    lines += ["Quarterly returns %:", "", "```", (100 * m["quarters"]).round(2).to_string(), "```", ""]
    report = "\n".join(lines)
    print(report)
    out = ROOT / "docs" / ("daytrade_backtest_synthetic.md" if args.synthetic else "daytrade_backtest.md")
    out.write_text(report)
    print(f"\n  report → {out}")
    return 0


def _has_tabulate() -> bool:
    try:
        import tabulate  # noqa: F401
        return True
    except ImportError:
        return False


def cmd_run(args):
    from bot.runner import run_once
    return run_once(dry=args.dry)


def cmd_fetch(args):
    cfg = load()
    for s, df in data.load_universe(cfg).items():
        print(f"  {s}: {len(df):,} bars  {df.index[0]} → {df.index[-1]}")
    return 0


def cmd_telegram(args):
    """Finds your chat id from the bot token (after you've messaged the bot once), then sends a test."""
    import os
    import requests
    tok = os.environ.get("TELEGRAM_BOT_TOKEN") or input("Paste your Telegram bot token: ").strip()
    r = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", timeout=15).json()
    chats = {m["message"]["chat"]["id"]: m["message"]["chat"].get("first_name", "?")
             for m in r.get("result", []) if "message" in m}
    if not chats:
        print("\n  No messages found. Open Telegram, send your bot any message (like 'hi'), then run this again.")
        return 1
    chat_id = list(chats)[0]
    print(f"\n  TELEGRAM_CHAT_ID = {chat_id}   ({chats[chat_id]})")
    ok = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                       json={"chat_id": chat_id, "text": "✅ CryptoBot alerts are wired up."}, timeout=15).ok
    print("  test message sent ✓" if ok else "  test message FAILED")
    print("\n  Add both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID as GitHub repository Secrets.")
    return 0 if ok else 1


def cmd_alpaca(args):
    """Verifies the Alpaca paper keys work and prints the account."""
    import os
    from alpaca.trading.client import TradingClient
    key = os.environ.get("ALPACA_API_KEY") or input("Alpaca API key: ").strip()
    sec = os.environ.get("ALPACA_SECRET_KEY") or input("Alpaca secret: ").strip()
    a = TradingClient(key, sec, paper=True).get_account()
    print(f"\n  paper account {a.account_number}  equity ${float(a.equity):,.2f}  cash ${float(a.cash):,.2f}  status {a.status}")
    print("  ✓ keys work. Add them as GitHub Secrets ALPACA_API_KEY / ALPACA_SECRET_KEY.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest"); b.add_argument("--synthetic", action="store_true"); b.set_defaults(fn=cmd_backtest)
    r = sub.add_parser("run"); r.add_argument("--dry", action="store_true"); r.set_defaults(fn=cmd_run)
    d = sub.add_parser("daytrade-backtest", help="QuantFlow clone on 5m bars (locked spec)")
    d.add_argument("--synthetic", action="store_true"); d.set_defaults(fn=cmd_daytrade_backtest)
    f = sub.add_parser("fetch"); f.set_defaults(fn=cmd_fetch)
    t = sub.add_parser("telegram", help="find chat id + send test message"); t.set_defaults(fn=cmd_telegram)
    al = sub.add_parser("alpaca", help="verify paper keys"); al.set_defaults(fn=cmd_alpaca)
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
