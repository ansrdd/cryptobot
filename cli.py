#!/usr/bin/env python3
"""CryptoBot command line.

  python cli.py backtest            # full history backtest + walk-forward → docs/backtest.html
  python cli.py backtest --synthetic  # same, on fake data (offline smoke test)
  python cli.py run                 # one paper-trading cycle (what the cron calls)
  python cli.py run --dry           # cycle without touching Alpaca (uses config equity)
  python cli.py fetch               # just refresh the data cache
  python cli.py daytrade-backtest   # QuantFlow clone on 5m bars → docs/daytrade_backtest.md
  python cli.py daytrade-backtest --synthetic  # same, on fake data (offline smoke test)
  python cli.py breakout1h-backtest            # 1h breakout DEV check → docs/breakout1h_dev.md
  python cli.py breakout1h-backtest --holdout  # the one shot verdict → docs/breakout1h_holdout.md
  python cli.py breakout1h-learn-backtest      # 1h breakout + learning filter DEV check → docs/breakout1h_learn_dev.md
  python cli.py breakout1h-learn-backtest --holdout  # its one shot verdict → docs/breakout1h_learn_holdout.md
  python cli.py qqq-backtest            # QuantFlow clone on QQQ 5m DEV check → docs/qqq_daytrade_dev.md
  python cli.py qqq-backtest --holdout  # its one shot verdict → docs/qqq_daytrade_holdout.md
  python cli.py adr-backtest            # ADR spec (TJR style) on QQQ + SPY 5m DEV check → docs/adr_dev.md
  python cli.py adr-backtest --holdout  # its one shot verdict → docs/adr_holdout.md
  python cli.py adr-backtest --loose    # ADR Loose spec DEV check → docs/adr_loose_dev.md (--holdout once)
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


def cmd_breakout1h_backtest(args):
    """1h breakout (docs/BREAKOUT1H_SPEC.md). Dev check by default; the holdout runs once, ever."""
    from bot import breakout1h
    cfg = breakout1h.load_config()
    period = "holdout" if args.holdout else "dev"
    if args.synthetic:
        n = 24 * 365 * 7
        bars = {}
        for i, s in enumerate(cfg["universe"]["symbols"]):
            df = data.synthetic(s, bars=n, seed=i * 13 + 7, start_price=30000 if "BTC" in s else 2000)
            df.index = pd.date_range(end="2026-10-27", periods=n, freq="1h", tz="UTC")
            bars[s] = df
        note, out = "SYNTHETIC DATA: smoke test only. No conclusions.", ROOT / "docs" / "breakout1h_synthetic.md"
    else:
        out = ROOT / "docs" / f"breakout1h_{period}.md"
        if period == "holdout" and out.exists():
            print(f"  refusing: the holdout already ran ({out}). It runs ONCE. See the spec.")
            return 1
        bars = breakout1h.load_bars(cfg)
        note = f"Coinbase 1h candles (research copy in {breakout1h.RESEARCH_DIR.relative_to(ROOT)})."
    text = breakout1h.report(cfg, bars, period, note)
    print(text)
    out.write_text(text)
    print(f"\n  report → {out}")
    return 0


def cmd_breakout1h_learn_backtest(args):
    """1h breakout + learning filter (docs/BREAKOUT1H_LEARN_SPEC.md). Dev by default; holdout once, ever."""
    from bot import breakout1h, breakout1h_learn
    base, learn = breakout1h_learn.load_config()
    period = "holdout" if args.holdout else "dev"
    if args.synthetic:
        n = 24 * 365 * 7
        bars = {}
        for i, s in enumerate(base["universe"]["symbols"]):
            df = data.synthetic(s, bars=n, seed=i * 13 + 7, start_price=30000 if "BTC" in s else 2000)
            df.index = pd.date_range(end="2026-10-27", periods=n, freq="1h", tz="UTC")
            bars[s] = df
        note, out = "SYNTHETIC DATA: smoke test only. No conclusions.", ROOT / "docs" / "breakout1h_learn_synthetic.md"
    else:
        out = ROOT / "docs" / f"breakout1h_learn_{period}.md"
        if period == "holdout" and out.exists():
            print(f"  refusing: the holdout already ran ({out}). It runs ONCE. See the spec.")
            return 1
        bars = breakout1h.load_bars(base)
        note = f"Coinbase 1h candles (research copy in {breakout1h.RESEARCH_DIR.relative_to(ROOT)})."
    text = breakout1h_learn.report(bars, base, learn, period, note)
    print(text)
    out.write_text(text)
    print(f"\n  report → {out}")
    return 0


def cmd_qqq_backtest(args):
    """QuantFlow clone on QQQ 5m (docs/QQQ_DAYTRADE_SPEC.md). Dev by default; holdout once, ever."""
    from bot import qqq_daytrade, stocks_data
    cfg = qqq_daytrade.load_config()
    period = "holdout" if args.holdout else "dev"
    if args.synthetic:
        bars = qqq_daytrade.synthetic(days=252 * 8)
        bars.index = bars.index + (pd.Timestamp("2016-01-04 14:30", tz="UTC") - bars.index[0]).floor("D")
        note, out = "SYNTHETIC DATA: smoke test only. No conclusions.", ROOT / "docs" / "qqq_daytrade_synthetic.md"
    else:
        out = ROOT / "docs" / f"qqq_daytrade_{period}.md"
        if period == "holdout" and out.exists():
            print(f"  refusing: the holdout already ran ({out}). It runs ONCE. See the spec.")
            return 1
        u = cfg["universe"]
        bars = stocks_data.load_5m(u["symbols"][0], u["history_start"])
        note = (f"Alpaca SIP 5m bars, regular hours, split + dividend adjusted (research copy in data/research). "
                f"SIP history starts {bars.index[0].tz_convert('America/New_York'):%Y-%m-%d}.")
    text = qqq_daytrade.report(cfg, bars, period, note)
    print(text)
    out.write_text(text)
    print(f"\n  report → {out}")
    return 0


def cmd_adr_backtest(args):
    """ADR spec, TJR style model on QQQ + SPY 5m (docs/ADR_SPEC.md). Dev by default; holdout once, ever."""
    from bot import adr, stocks_data
    tag = "adr_loose" if args.loose else "adr"
    spec = "docs/ADR_LOOSE_SPEC.md" if args.loose else "docs/ADR_SPEC.md"
    cfg = adr.load_config(ROOT / f"config_{tag}.yaml")
    period = "holdout" if args.holdout else "dev"
    out = ROOT / "docs" / f"{tag}_{period}.md"
    if period == "holdout" and out.exists():
        print(f"  refusing: the holdout already ran ({out}). It runs ONCE. See the spec.")
        return 1
    u = cfg["universe"]
    bars = {s: stocks_data.load_5m_ext(s, u["history_start"], u["premarket"]["start"]) for s in u["symbols"]}
    first = max(b.index[0] for b in bars.values()).tz_convert("America/New_York")
    note = (f"Alpaca SIP 5m bars incl. premarket from {u['premarket']['start']}, split + dividend adjusted "
            f"(research copy in data/research). SIP history starts {first:%Y-%m-%d}.")
    text = adr.report(cfg, bars, period, note, name="ADR Loose spec (looser TJR style)" if args.loose else "ADR spec (TJR style)",
                      spec=spec, config=f"config_{tag}.yaml")
    print(text)
    out.write_text(text)
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
    bo = sub.add_parser("breakout1h-backtest", help="1h breakout: dev check, or --holdout once")
    bo.add_argument("--holdout", action="store_true", help="the one shot verdict on 2025+ data")
    bo.add_argument("--synthetic", action="store_true"); bo.set_defaults(fn=cmd_breakout1h_backtest)
    bl = sub.add_parser("breakout1h-learn-backtest", help="1h breakout + learning filter: dev check, or --holdout once")
    bl.add_argument("--holdout", action="store_true", help="the one shot verdict on 2025+ data")
    bl.add_argument("--synthetic", action="store_true"); bl.set_defaults(fn=cmd_breakout1h_learn_backtest)
    qq = sub.add_parser("qqq-backtest", help="QuantFlow clone on QQQ 5m: dev check, or --holdout once")
    qq.add_argument("--holdout", action="store_true", help="the one shot verdict on 2023+ data")
    qq.add_argument("--synthetic", action="store_true"); qq.set_defaults(fn=cmd_qqq_backtest)
    ad = sub.add_parser("adr-backtest", help="ADR spec (TJR style) on QQQ + SPY 5m: dev check, or --holdout once")
    ad.add_argument("--holdout", action="store_true", help="the one shot verdict on 2023+ data")
    ad.add_argument("--loose", action="store_true", help="the ADR Loose spec (docs/ADR_LOOSE_SPEC.md)")
    ad.set_defaults(fn=cmd_adr_backtest)
    f = sub.add_parser("fetch"); f.set_defaults(fn=cmd_fetch)
    t = sub.add_parser("telegram", help="find chat id + send test message"); t.set_defaults(fn=cmd_telegram)
    al = sub.add_parser("alpaca", help="verify paper keys"); al.set_defaults(fn=cmd_alpaca)
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
