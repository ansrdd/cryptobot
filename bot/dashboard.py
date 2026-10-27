"""Static HTML dashboard. P&L first. No external JS, works on GitHub Pages.

render(equity, benchmark, trades_df, metrics, meta) -> HTML string
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

PALETTE = {
    "light": {"surface": "#fcfcfb", "card": "#ffffff", "border": "#e6e5e1", "text": "#0b0b0b",
              "text2": "#52514e", "muted": "#8a8984", "bot": "#2a78d6", "bench": "#9a9892",
              "good": "#008300", "bad": "#c93a39", "grid": "#ecebe7", "trend": "#2a78d6", "meanrev": "#eb6834"},
    "dark":  {"surface": "#141413", "card": "#1a1a19", "border": "#2c2c2a", "text": "#f0efec",
              "text2": "#c3c2b7", "muted": "#8a8984", "bot": "#3987e5", "bench": "#8a8984",
              "good": "#3fb950", "bad": "#e66767", "grid": "#262624", "trend": "#3987e5", "meanrev": "#d95926"},
}


def _fmt_money(x: float, sign: bool = False) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    s = f"{abs(x):,.0f}"
    if sign:
        return ("+" if x >= 0 else "−") + "$" + s
    return "$" + s


def _fmt_pct(x: float, sign: bool = True, d: int = 1) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "—"
    return (("+" if x >= 0 else "−") if sign else "") + f"{abs(x):.{d}f}%"


def _fmt_num(x: float, d: int = 2) -> str:
    if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
        return "—"
    return f"{x:.{d}f}"


def _downsample(s: pd.Series, n: int = 1200) -> pd.Series:
    if len(s) <= n:
        return s
    step = int(np.ceil(len(s) / n))
    return s.iloc[::step]


def _line_chart(series: dict[str, pd.Series], colors: dict[str, str], height: int = 260,
                yfmt: str = "money", chart_id: str = "c1") -> str:
    """Inline SVG multi-line chart with hover crosshair. All series share an index."""
    W, H, padL, padR, padT, padB = 900, height, 64, 16, 16, 28
    keys = list(series)
    s0 = series[keys[0]]
    xs = s0.index
    allv = np.concatenate([series[k].values for k in keys])
    ymin, ymax = float(np.nanmin(allv)), float(np.nanmax(allv))
    if ymin == ymax:
        ymax = ymin + 1
    pad = (ymax - ymin) * 0.06
    ymin, ymax = ymin - pad, ymax + pad
    n = len(xs)

    def X(i):
        return padL + (W - padL - padR) * i / max(n - 1, 1)

    def Y(v):
        return padT + (H - padT - padB) * (1 - (v - ymin) / (ymax - ymin))

    paths = []
    for k in keys:
        pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(series[k].values) if not np.isnan(v))
        paths.append(f'<polyline fill="none" stroke="{colors[k]}" stroke-width="2" stroke-linejoin="round" points="{pts}"/>')
    # y grid: 4 lines
    grid = []
    for t in np.linspace(ymin + pad, ymax - pad, 4):
        label = _fmt_money(t) if yfmt == "money" else f"{t:.0f}%"
        grid.append(f'<line x1="{padL}" x2="{W-padR}" y1="{Y(t):.1f}" y2="{Y(t):.1f}" class="grid"/>'
                    f'<text x="{padL-8}" y="{Y(t)+4:.1f}" text-anchor="end" class="axis">{label}</text>')
    # x labels: ~6
    xl = []
    for i in np.linspace(0, n - 1, 6).astype(int):
        xl.append(f'<text x="{X(i):.1f}" y="{H-8}" text-anchor="middle" class="axis">{xs[i].strftime("%b %d %y")}</text>')
    data_json = json.dumps({
        "x": [t.strftime("%Y-%m-%d %H:%M") for t in xs],
        "series": {k: [None if np.isnan(v) else round(float(v), 2) for v in series[k].values] for k in keys},
        "colors": colors, "padL": padL, "padR": padR, "W": W, "fmt": yfmt,
    })
    return f'''
<div class="chart" data-chart="{chart_id}">
<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" id="{chart_id}">
{''.join(grid)}{''.join(xl)}{''.join(paths)}
<line class="xhair" x1="0" x2="0" y1="{padT}" y2="{H-padB}" style="display:none"/>
</svg>
<div class="tip" style="display:none"></div>
<script type="application/json" id="{chart_id}-data">{data_json}</script>
</div>'''


def _table(df: pd.DataFrame, cols: list[tuple[str, str, str]], max_rows: int | None = None) -> str:
    """cols: (column, header, kind) kind in money|pct|num|text|dt|int"""
    rows = []
    it = df.iloc[::-1] if max_rows else df
    if max_rows:
        it = it.head(max_rows)
    for _, r in it.iterrows():
        cells = []
        for c, _h, kind in cols:
            v = r[c]
            cls = ""
            if kind == "money":
                cls = "good" if v > 0 else "bad" if v < 0 else ""
                txt = _fmt_money(v, sign=True)
            elif kind == "pct":
                cls = "good" if v > 0 else "bad" if v < 0 else ""
                txt = _fmt_pct(v)
            elif kind == "num":
                txt = _fmt_num(v)
            elif kind == "int":
                txt = f"{int(v)}"
            elif kind == "dt":
                txt = pd.Timestamp(v).strftime("%Y-%m-%d %H:%M")
            elif kind == "price":
                txt = f"{v:,.2f}"
            else:
                txt = html.escape(str(v))
            cells.append(f'<td class="{cls} {kind}">{txt}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    head = "".join(f'<th class="{k}">{html.escape(h)}</th>' for _, h, k in cols)
    return f'<table><thead><tr>{head}</tr></thead><tbody>{"".join(rows) or "<tr><td colspan=99 class=muted>No trades yet</td></tr>"}</tbody></table>'


def render(equity: pd.Series, benchmark: pd.Series, trades: pd.DataFrame, m: dict,
           meta: dict, title: str = "CryptoBot — Paper") -> str:
    """meta: {mode, starting_equity, last_run, status, status_detail, open_positions: [...], notes: str, walk_forward: df|None}"""
    L, D = PALETTE["light"], PALETTE["dark"]
    eq0 = meta.get("starting_equity", equity.iloc[0])
    cur = float(equity.iloc[-1])
    pnl = cur - eq0
    pnl_pct = 100 * pnl / eq0
    bench_pct = m.get("benchmark_return_pct", float("nan"))

    # period P&L
    def period_pnl(hours: int) -> float:
        cutoff = equity.index[-1] - pd.Timedelta(hours=hours)
        past = equity[equity.index <= cutoff]
        return cur - float(past.iloc[-1]) if len(past) else float("nan")

    pnl_d, pnl_w, pnl_m = period_pnl(24), period_pnl(24 * 7), period_pnl(24 * 30)
    dd = (equity / equity.cummax() - 1) * 100
    bench_dd = (benchmark / benchmark.cummax() - 1) * 100
    cur_dd = float(dd.iloc[-1])

    eq_ds, bm_ds = _downsample(equity), _downsample(benchmark.reindex(equity.index).ffill())
    eq_chart = _line_chart({"Bot": eq_ds, "Buy & hold": bm_ds}, {"Bot": "var(--bot)", "Buy & hold": "var(--bench)"},
                           height=280, chart_id="equity")
    dd_chart = _line_chart({"Bot": _downsample(dd), "Buy & hold": _downsample(bench_dd.reindex(equity.index).ffill())},
                           {"Bot": "var(--bot)", "Buy & hold": "var(--bench)"}, height=160, yfmt="pct", chart_id="dd")

    # breakdowns
    if len(trades):
        realized = trades.pnl.sum()
        by_strat = trades.groupby("strategy").agg(trades=("pnl", "size"), pnl=("pnl", "sum"),
                                                   win_rate=("pnl", lambda s: 100 * (s > 0).mean())).reset_index()
        by_sym = trades.groupby("symbol").agg(trades=("pnl", "size"), pnl=("pnl", "sum"),
                                               win_rate=("pnl", lambda s: 100 * (s > 0).mean())).reset_index()
        by_reason = trades.groupby("reason").agg(trades=("pnl", "size"), pnl=("pnl", "sum")).reset_index()
    else:
        realized = 0.0
        by_strat = by_sym = by_reason = pd.DataFrame(columns=["strategy", "symbol", "reason", "trades", "pnl", "win_rate"])
    unrealized = pnl - realized

    status = meta.get("status", "ok")
    status_cls = {"ok": "good", "killed": "bad", "halted": "warn", "stale": "warn", "error": "bad"}.get(status, "")
    status_txt = {"ok": "Running", "killed": "KILL SWITCH — bot disabled", "halted": "Paused for today (daily loss limit)",
                  "stale": "Data stale", "error": "Last run failed"}.get(status, status)

    pos_rows = ""
    for p in meta.get("open_positions", []):
        upnl = p.get("unrealized_pnl", 0.0)
        pos_rows += (f"<tr><td>{html.escape(p['symbol'])}</td><td>{html.escape(p['strategy'])}</td>"
                     f"<td class=num>{p['units']:.5f}</td><td class=price>{p['entry_price']:,.2f}</td>"
                     f"<td class=price>{p.get('current_price', 0):,.2f}</td><td class=price>{p['stop']:,.2f}</td>"
                     f"<td class='money {'good' if upnl>=0 else 'bad'}'>{_fmt_money(upnl, True)}</td></tr>")
    if not pos_rows:
        pos_rows = "<tr><td colspan=7 class=muted>Flat — no open positions</td></tr>"

    wf_html = ""
    wf = meta.get("walk_forward")
    if wf is not None and len(wf):
        beat = int(wf.beat_bench.sum())
        wf_html = f'''
<section class="card">
  <h2>Walk-forward consistency <span class="sub">{beat}/{len(wf)} out-of-sample windows beat buy-and-hold</span></h2>
  {_table(wf, [("window_start","From","text"),("window_end","To","text"),("return_pct","Bot","pct"),
               ("bench_pct","Buy & hold","pct"),("sharpe","Sharpe","num"),("max_dd_pct","Max DD","pct"),("trades","Trades","int")])}
  <p class="note">Each window starts fresh at ${eq0:,.0f}. A real edge shows up in most windows, not just the good ones.</p>
</section>'''

    notes = html.escape(meta.get("notes", ""))
    last_run = meta.get("last_run") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    css_vars = lambda P: "".join(f"--{k}:{v};" for k, v in P.items())
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{color-scheme:light;{css_vars(L)}}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{color-scheme:dark;{css_vars(D)}}}}}
:root[data-theme=dark]{{color-scheme:dark;{css_vars(D)}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--surface);color:var(--text);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif;padding:16px}}
.wrap{{max-width:1100px;margin:0 auto}}
header{{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 16px;margin:4px 0 16px}}
header h1{{font-size:20px;margin:0;font-weight:650}}
.pill{{font-size:12px;padding:3px 10px;border-radius:999px;border:1px solid var(--border);color:var(--text2)}}
.pill.good{{color:var(--good);border-color:var(--good)}}.pill.bad{{color:var(--bad);border-color:var(--bad)}}.pill.warn{{color:#b26a00;border-color:#b26a00}}
.meta{{margin-left:auto;font-size:12px;color:var(--muted)}}
.hero{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:12px}}
.tile{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px 16px}}
.tile .lbl{{font-size:12px;color:var(--text2);text-transform:uppercase;letter-spacing:.04em}}
.tile .val{{font-size:26px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums}}
.tile .val.big{{font-size:34px}}
.tile .sub{{font-size:12px;color:var(--muted);margin-top:2px}}
.good{{color:var(--good)}}.bad{{color:var(--bad)}}.muted{{color:var(--muted)}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:12px}}
.card h2{{font-size:15px;margin:0 0 10px;font-weight:650;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}}
.card h2 .sub{{font-size:12px;font-weight:400;color:var(--muted)}}
.legend{{display:flex;gap:16px;font-size:12px;color:var(--text2);margin-bottom:6px}}
.legend i{{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:6px}}
.chart{{position:relative}}
.chart svg{{width:100%;height:auto;display:block;overflow:visible}}
.grid{{stroke:var(--grid);stroke-width:1}}
.axis{{fill:var(--muted);font-size:11px}}
.xhair{{stroke:var(--text2);stroke-width:1;stroke-dasharray:3 3}}
.tip{{position:absolute;pointer-events:none;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:6px 10px;font-size:12px;box-shadow:0 4px 14px rgba(0,0,0,.12);white-space:nowrap}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}}
table{{width:100%;border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums}}
th{{text-align:left;font-weight:600;color:var(--text2);font-size:12px;padding:6px 8px;border-bottom:1px solid var(--border)}}
td{{padding:6px 8px;border-bottom:1px solid var(--grid)}}
th.money,td.money,th.pct,td.pct,th.num,td.num,th.int,td.int,th.price,td.price{{text-align:right}}
.note{{font-size:12px;color:var(--muted);margin:8px 0 0}}
.scroll{{max-height:420px;overflow:auto}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px 16px;font-size:13px}}
.stats div span{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em}}
.stats div b{{font-weight:600;font-variant-numeric:tabular-nums}}
footer{{font-size:12px;color:var(--muted);margin:16px 4px}}
</style></head><body><div class="wrap">
<header>
  <h1>{html.escape(title)}</h1>
  <span class="pill {status_cls}">{status_txt}</span>
  <span class="pill">{html.escape(str(meta.get("mode","paper")).upper())}</span>
  <span class="meta">Last run {html.escape(last_run)}</span>
</header>

<section class="hero">
  <div class="tile"><div class="lbl">Total P&amp;L</div>
    <div class="val big {'good' if pnl>=0 else 'bad'}">{_fmt_money(pnl, True)}</div>
    <div class="sub {'good' if pnl>=0 else 'bad'}">{_fmt_pct(pnl_pct)} · after fees &amp; slippage</div></div>
  <div class="tile"><div class="lbl">Balance</div><div class="val">{_fmt_money(cur)}</div><div class="sub">started {_fmt_money(eq0)}</div></div>
  <div class="tile"><div class="lbl">vs Buy &amp; hold</div>
    <div class="val {'good' if pnl_pct>=bench_pct else 'bad'}">{_fmt_pct(pnl_pct - bench_pct)}</div>
    <div class="sub">B&amp;H {_fmt_pct(bench_pct)} same period</div></div>
  <div class="tile"><div class="lbl">Today</div><div class="val {'good' if pnl_d>=0 else 'bad'}">{_fmt_money(pnl_d, True)}</div><div class="sub">last 24h</div></div>
  <div class="tile"><div class="lbl">This week</div><div class="val {'good' if pnl_w>=0 else 'bad'}">{_fmt_money(pnl_w, True)}</div><div class="sub">last 7d</div></div>
  <div class="tile"><div class="lbl">This month</div><div class="val {'good' if pnl_m>=0 else 'bad'}">{_fmt_money(pnl_m, True)}</div><div class="sub">last 30d</div></div>
  <div class="tile"><div class="lbl">Drawdown</div><div class="val {'bad' if cur_dd< -1 else ''}">{_fmt_pct(cur_dd, sign=False)}</div>
    <div class="sub">max {_fmt_pct(m.get("max_drawdown_pct"), sign=False)} · kill at {meta.get("kill_pct", 15)}%</div></div>
</section>

<section class="card">
  <h2>Equity curve <span class="sub">bot vs. buying and holding {html.escape(meta.get("benchmark","BTC"))}</span></h2>
  <div class="legend"><span><i style="background:var(--bot)"></i>Bot</span><span><i style="background:var(--bench)"></i>Buy &amp; hold</span></div>
  {eq_chart}
</section>

<section class="card">
  <h2>Drawdown from peak <span class="sub">how deep the holes get — this is the stability metric</span></h2>
  {dd_chart}
</section>

<div class="grid2">
<section class="card"><h2>Performance</h2>
  <div class="stats">
    <div><span>Realized</span><b class="{'good' if realized>=0 else 'bad'}">{_fmt_money(realized, True)}</b></div>
    <div><span>Unrealized</span><b class="{'good' if unrealized>=0 else 'bad'}">{_fmt_money(unrealized, True)}</b></div>
    <div><span>Sharpe</span><b>{_fmt_num(m.get("sharpe"))}</b></div>
    <div><span>Calmar</span><b>{_fmt_num(m.get("calmar"))}</b></div>
    <div><span>Profit factor</span><b>{_fmt_num(m.get("profit_factor"))}</b></div>
    <div><span>Win rate</span><b>{_fmt_pct(m.get("win_rate_pct"), sign=False)}</b></div>
    <div><span>Trades</span><b>{m.get("n_trades", 0)}</b></div>
    <div><span>Avg win</span><b class="good">{_fmt_money(m.get("avg_win", 0), True)}</b></div>
    <div><span>Avg loss</span><b class="bad">{_fmt_money(m.get("avg_loss", 0), True)}</b></div>
    <div><span>Longest underwater</span><b>{_fmt_num(m.get("longest_underwater_days"), 0)} d</b></div>
    <div><span>CAGR</span><b>{_fmt_pct(m.get("cagr_pct"))}</b></div>
    <div><span>B&amp;H max DD</span><b>{_fmt_pct(m.get("benchmark_max_dd_pct"), sign=False)}</b></div>
  </div>
</section>
<section class="card"><h2>Open positions</h2>
  <table><thead><tr><th>Symbol</th><th>Strategy</th><th class=num>Units</th><th class=price>Entry</th><th class=price>Now</th><th class=price>Stop</th><th class=money>Unrealized</th></tr></thead>
  <tbody>{pos_rows}</tbody></table>
</section>
</div>

<div class="grid2">
<section class="card"><h2>By strategy</h2>
  {_table(by_strat, [("strategy","Strategy","text"),("trades","Trades","int"),("pnl","P&L","money"),("win_rate","Win %","num")])}</section>
<section class="card"><h2>By symbol</h2>
  {_table(by_sym, [("symbol","Symbol","text"),("trades","Trades","int"),("pnl","P&L","money"),("win_rate","Win %","num")])}</section>
<section class="card"><h2>By exit reason</h2>
  {_table(by_reason, [("reason","Reason","text"),("trades","Trades","int"),("pnl","P&L","money")])}</section>
</div>

{wf_html}

<section class="card">
  <h2>Trade log <span class="sub">most recent first · {len(trades)} total</span></h2>
  <div class="scroll">{_table(trades, [("exit_ts","Closed","dt"),("symbol","Symbol","text"),("strategy","Strategy","text"),
      ("entry_price","Entry","price"),("exit_price","Exit","price"),("units","Units","num"),("pnl","P&L","money"),
      ("pnl_pct","%","pct"),("reason","Exit reason","text"),("bars_held","Bars held","int")], max_rows=300)}</div>
</section>

{f'<section class="card"><h2>Notes</h2><p class="note" style="font-size:13px;color:var(--text2)">{notes}</p></section>' if notes else ''}

<footer>P&amp;L is net of {meta.get("fee_bps", 25)} bps fees + {meta.get("slip_bps", 5)} bps slippage per side, applied even on paper so the numbers stay honest. Paper trading only. Not financial advice.</footer>
</div>
<script>
document.querySelectorAll('.chart').forEach(function(ch){{
  var svg=ch.querySelector('svg'), tip=ch.querySelector('.tip'), xh=svg.querySelector('.xhair');
  var d=JSON.parse(ch.querySelector('script[type="application/json"]').textContent);
  var n=d.x.length, keys=Object.keys(d.series);
  function fmt(v){{ if(v==null) return '—'; return d.fmt==='money' ? '$'+Math.round(v).toLocaleString() : v.toFixed(2)+'%'; }}
  svg.addEventListener('mousemove',function(e){{
    var r=svg.getBoundingClientRect(); var px=(e.clientX-r.left)/r.width*d.W;
    var i=Math.round((px-d.padL)/(d.W-d.padL-d.padR)*(n-1)); i=Math.max(0,Math.min(n-1,i));
    var sx=d.padL+(d.W-d.padL-d.padR)*i/Math.max(n-1,1);
    xh.setAttribute('x1',sx); xh.setAttribute('x2',sx); xh.style.display='';
    tip.innerHTML='<b>'+d.x[i]+'</b><br>'+keys.map(function(k){{return '<span style="color:'+d.colors[k]+'">●</span> '+k+' '+fmt(d.series[k][i]);}}).join('<br>');
    tip.style.display=''; var lx=(e.clientX-r.left); tip.style.left=(lx>r.width*0.7? lx-tip.offsetWidth-12 : lx+12)+'px'; tip.style.top='8px';
  }});
  svg.addEventListener('mouseleave',function(){{xh.style.display='none';tip.style.display='none';}});
}});
</script>
</body></html>'''
