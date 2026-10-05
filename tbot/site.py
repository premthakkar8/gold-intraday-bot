"""Static dashboard for GitHub Pages."""

from __future__ import annotations

import html
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from tbot.strategy import STYLES


def page_data(brief, calendar, blackout, now: datetime, signal_state: dict, memory, symbol: str) -> dict:
    chart, macro, news = brief.chart, brief.macro, brief.news
    graded = [obs for obs in memory.observations if obs.outcome in ("win", "loss", "scratch")]
    wins = sum(obs.outcome == "win" for obs in graded)
    losses = sum(obs.outcome == "loss" for obs in graded)
    scratches = sum(obs.outcome == "scratch" for obs in graded)
    rs = [obs.r_multiple for obs in graded if obs.r_multiple is not None]
    events = []
    for event in calendar.upcoming(now, hours=36):
        events.append(
            {
                "title": event.title,
                "when": event.when.strftime("%a %H:%M UTC"),
                "impact": event.impact,
                "forecast": event.forecast,
                "previous": event.previous,
            }
        )
    return {
        "updated": now.strftime("%Y-%m-%d %H:%M UTC"),
        "symbol": symbol,
        "price": chart.last_price,
        "stance": signal_state.get("stance") or "",
        "setups": signal_state.get("setups", []),
        "active": [item for item in signal_state.get("active", []) if not item.get("closed")],
        "blackout": f"{blackout.title} at {blackout.when:%H:%M UTC}" if blackout is not None else None,
        "chart": {
            "structure": chart.structure,
            "htf": brief.htf_structure,
            "session": chart.session,
            "vwap": chart.vwap,
            "atr": chart.atr,
            "day_low": chart.day_low,
            "day_high": chart.day_high,
            "range_pos": chart.range_pos,
        },
        "dxy": {
            "ok": macro.dxy_available and macro.dxy_last is not None,
            "last": macro.dxy_last,
            "pct": macro.dxy_session_pct,
            "state": macro.dxy_state,
            "corr": macro.gold_dxy_corr if macro.dxy_overlap >= 12 else None,
        },
        "oil": {
            "ok": macro.oil_available and macro.oil_last is not None,
            "last": macro.oil_last,
            "pct": macro.oil_session_pct,
            "state": macro.oil_state,
            "corr": macro.gold_oil_corr if macro.oil_overlap >= 12 else None,
        },
        "news": {
            "usd": news.usd_score,
            "oil": news.oil_score,
            "shock": news.shock,
            "usd_headlines": news.usd_headlines[:3],
            "oil_headlines": news.oil_headlines[:3],
        },
        "events": events,
        "scorecard": [
            {"style": STYLES.get(row.style, row.style), "bias": row.bias, "score": row.adjusted, "trust": row.multiplier}
            for row in sorted(brief.plan.scorecard, key=lambda item: item.adjusted, reverse=True)
        ],
        "record": {
            "graded": len(graded),
            "wins": wins,
            "losses": losses,
            "scratches": scratches,
            "avg_r": (sum(rs) / len(rs)) if rs else None,
        },
    }


def build_site(site_dir: Path, data: dict, chart: Path | None, brief_text: str, tv_symbol: str | None = None) -> Path:
    site_dir.mkdir(parents=True, exist_ok=True)
    stamp = int(datetime.now(timezone.utc).timestamp())
    plan_image = ""
    if chart is not None and chart.exists():
        shutil.copyfile(chart, site_dir / "chart.png")
        plan_image = f'<img src="chart.png?v={stamp}" alt="Strategy levels on the gold chart">'
    (site_dir / "data.json").write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    page = _page(data, plan_image, brief_text, tv_symbol)
    (site_dir / "index.html").write_text(page, encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    return site_dir / "index.html"


def _e(value) -> str:
    return html.escape(str(value))


def _signed(value: float | None, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f}{suffix}"


def _tone(value: float | None, invert: bool = False) -> str:
    if value is None or abs(value) < 1e-9:
        return "flat"
    up = value > 0
    if invert:
        up = not up
    return "up" if up else "down"


def _setup_card(item: dict) -> str:
    side = item["side"]
    tags = []
    if item.get("ready"):
        tags.append('<span class="tag tag-ready">Strategy trade</span>')
    elif item.get("favored"):
        tags.append('<span class="tag tag-fav">Favored</span>')
    else:
        tags.append('<span class="tag">Other side</span>')
    tp2 = ""
    if item.get("tp2") is not None:
        tp2 = f'<div class="lvl"><span>TP2</span><b class="tp">{item["tp2"]:.2f}</b></div>'
    score = max(0.0, min(1.0, float(item.get("score", 0))))
    return f"""
<article class="setup {side.lower()}">
  <header>
    <div class="side">{_e(side)} <small>XAUUSD</small></div>
    {''.join(tags)}
  </header>
  <div class="levels">
    <div class="lvl"><span>Entry</span><b>{item['entry_low']:.2f} – {item['entry_high']:.2f}</b></div>
    <div class="lvl"><span>Stop loss</span><b class="sl">{item['sl']:.2f}</b></div>
    <div class="lvl"><span>TP1</span><b class="tp">{item['tp1']:.2f}</b></div>
    {tp2}
  </div>
  <footer>
    <div><span>R:R</span><b>1:{item['rr']:.1f}</b></div>
    <div><span>Risk</span><b>${item['risk_usd']:.2f}</b></div>
    <div><span>Size</span><b>{item['units']:g} oz</b></div>
    <div class="meter" title="Strategy score {score:.2f}"><i style="width:{score * 100:.0f}%"></i><em>score {score:.2f}</em></div>
  </footer>
  <p class="why">{_e(STYLES.get(item.get('style', ''), item.get('style', '')) if item.get('style') in STYLES else item.get('style', ''))}</p>
</article>"""


def _tile(label: str, value: str, sub: str = "", tone: str = "") -> str:
    return f'<div class="tile {tone}"><span>{_e(label)}</span><b>{value}</b><small>{sub}</small></div>'


def _page(data: dict, plan_image: str, brief_text: str, tv_symbol: str | None) -> str:
    chart = data["chart"]
    dxy, oil, news, record = data["dxy"], data["oil"], data["news"], data["record"]
    setups = sorted(data["setups"], key=lambda item: (not item.get("ready"), not item.get("favored")))
    favored = next((item for item in setups if item.get("ready") or item.get("favored")), None)

    if favored is not None:
        head_side = favored["side"]
        head_class = favored["side"].lower()
        head_line = (
            f"{head_side} entry {favored['entry_low']:.2f} – {favored['entry_high']:.2f} · "
            f"SL {favored['sl']:.2f} · TP {favored['tp1']:.2f}"
        )
    else:
        head_side, head_class, head_line = "WAIT", "flat", "No setup fits the risk rules right now."

    in_zone = ""
    if favored is not None:
        if favored["entry_low"] <= data["price"] <= favored["entry_high"]:
            in_zone = '<span class="pill pill-live">Price is in the entry zone</span>'
        else:
            gap = min(abs(data["price"] - favored["entry_low"]), abs(data["price"] - favored["entry_high"]))
            in_zone = f'<span class="pill">{gap:.2f} away from entry</span>'

    active_html = ""
    for item in data["active"]:
        active_html += (
            f'<div class="banner live">ACTIVE {_e(item["side"])} from {item.get("entry", 0):.2f} · '
            f'SL {item["sl"]:.2f} · TP {item["tp1"]:.2f}</div>'
        )
    if data["blackout"]:
        active_html += f'<div class="banner warn">US event blackout: {_e(data["blackout"])}. Do not enter until it passes.</div>'

    range_pct = max(0.0, min(1.0, chart["range_pos"])) * 100
    tiles = "".join(
        [
            _tile("Gold", f"{data['price']:.2f}", f"VWAP {chart['vwap']:.2f}"),
            _tile("Day range", f"{chart['day_low']:.2f} – {chart['day_high']:.2f}",
                  f'<i class="range"><i style="left:{range_pct:.0f}%"></i></i>'),
            _tile("Trend", f"{_e(chart['structure'])} / {_e(chart['htf'])}", "5-minute / 1-hour"),
            _tile("ATR (5m)", f"{chart['atr']:.2f}", f"{_e(chart['session'])} session"),
            _tile(
                "Dollar index",
                f"{dxy['last']:.2f}" if dxy["ok"] else "—",
                f"{_signed(dxy['pct'], 2, '%')} today · corr {_signed(dxy['corr'])}" if dxy["ok"] else "unavailable",
                _tone(dxy["pct"], invert=True) if dxy["ok"] else "",
            ),
            _tile(
                "WTI oil",
                f"{oil['last']:.2f}" if oil["ok"] else "—",
                f"{_signed(oil['pct'], 2, '%')} today · corr {_signed(oil['corr'])}" if oil["ok"] else "unavailable",
                _tone(oil["pct"]) if oil["ok"] else "",
            ),
            _tile(
                "News: dollar",
                _signed(news["usd"]),
                "hawkish, gold headwind" if news["usd"] >= 0.35 else "dovish, gold tailwind" if news["usd"] <= -0.35 else "neutral",
                _tone(news["usd"], invert=True),
            ),
            _tile(
                "News: oil",
                _signed(news["oil"]),
                ("supply shock" if news["oil"] >= 0.35 else "bearish crude" if news["oil"] <= -0.35 else "neutral")
                + (" · fresh shock" if news["shock"] else ""),
                _tone(news["oil"]),
            ),
        ]
    )

    events = data["events"]
    if events:
        rows = "".join(
            f'<li><span class="imp imp-{_e(ev["impact"].lower())}">{_e(ev["impact"])}</span>'
            f'<b>{_e(ev["title"])}</b><time>{_e(ev["when"])}</time>'
            f'<small>{_e(("forecast " + ev["forecast"]) if ev["forecast"] else "")} {_e(("· prev " + ev["previous"]) if ev["previous"] else "")}</small></li>'
            for ev in events[:6]
        )
        calendar_html = f'<ul class="events">{rows}</ul>'
    else:
        calendar_html = '<p class="muted">No high or medium impact US releases in the next 36 hours.</p>'

    headlines = ""
    for label, items in (("Dollar", news["usd_headlines"]), ("Oil", news["oil_headlines"])):
        for title in items[:2]:
            headlines += f'<li><span class="hl-tag">{label}</span>{_e(title)}</li>'
    headlines_html = f'<ul class="headlines">{headlines}</ul>' if headlines else '<p class="muted">No scored headlines.</p>'

    score_rows = "".join(
        f'<li><span>{_e(row["style"])}</span><i class="bar"><i style="width:{max(0, min(1, row["score"])) * 100:.0f}%"></i></i>'
        f'<b>{row["score"]:.2f}</b><small>{_e(row["bias"])}</small></li>'
        for row in data["scorecard"]
    )

    avg_r = "—" if record["avg_r"] is None else f"{record['avg_r']:+.2f}R"
    win_rate = "—"
    decided = record["wins"] + record["losses"]
    if decided:
        win_rate = f"{record['wins'] / decided:.0%}"
    record_html = "".join(
        [
            _tile("Graded plans", str(record["graded"])),
            _tile("Win rate", win_rate, f"{record['wins']}W · {record['losses']}L · {record['scratches']} scratch"),
            _tile("Average result", avg_r, "per graded plan"),
        ]
    )

    setup_cards = "".join(_setup_card(item) for item in setups) or '<p class="muted">No setup fits the $5 risk rule right now.</p>'

    widget = ""
    if tv_symbol:
        widget = f"""
<div class="tv">
  <div id="tv_gold"></div>
  <script src="https://s3.tradingview.com/tv.js"></script>
  <script>
  new TradingView.widget({{"autosize": true, "symbol": "{_e(tv_symbol)}", "interval": "5", "timezone": "Etc/UTC",
    "theme": "dark", "style": "1", "locale": "en", "allow_symbol_change": false, "hide_side_toolbar": true,
    "backgroundColor": "#11151c", "gridColor": "rgba(255,255,255,0.04)", "container_id": "tv_gold"}});
  </script>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>Gold signal · {data['price']:.2f} · {_e(head_side)}</title>
<style>
:root {{ --bg:#0b0e13; --panel:#11151c; --line:#1e2530; --text:#e8ebf0; --muted:#8a94a6; --gold:#e3b341;
  --buy:#2fbf71; --sell:#ef5350; --warn:#f0a020; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 Inter, "Segoe UI", system-ui, sans-serif; }}
main {{ max-width:1180px; margin:0 auto; padding:28px 20px 72px; }}
.top {{ display:flex; justify-content:space-between; align-items:flex-end; gap:16px; flex-wrap:wrap; margin-bottom:22px; }}
.brand {{ font-size:13px; letter-spacing:.14em; text-transform:uppercase; color:var(--gold); font-weight:700; }}
.brand small {{ color:var(--muted); letter-spacing:.05em; font-weight:500; margin-left:8px; }}
.price {{ font-size:44px; font-weight:700; letter-spacing:-.02em; line-height:1.1; }}
.updated {{ color:var(--muted); font-size:13px; text-align:right; }}
.hero {{ display:grid; grid-template-columns:1fr; gap:14px; margin-bottom:18px; }}
.call {{ background:linear-gradient(135deg, #151b24, #10141b); border:1px solid var(--line); border-radius:16px; padding:22px 24px; }}
.call .label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.1em; }}
.call .big {{ font-size:40px; font-weight:800; margin:4px 0 6px; }}
.call.buy .big {{ color:var(--buy); }} .call.sell .big {{ color:var(--sell); }} .call.flat .big {{ color:var(--muted); }}
.call .line {{ font-size:17px; font-weight:600; }}
.call .stance {{ color:var(--muted); margin:12px 0 0; }}
.pill {{ display:inline-block; margin-top:12px; padding:4px 10px; border-radius:999px; background:#1b2230; color:var(--muted); font-size:12px; }}
.pill-live {{ background:rgba(47,191,113,.15); color:var(--buy); }}
.banner {{ margin-top:12px; padding:10px 14px; border-radius:10px; font-weight:600; font-size:14px; }}
.banner.live {{ background:rgba(47,191,113,.12); color:var(--buy); border:1px solid rgba(47,191,113,.3); }}
.banner.warn {{ background:rgba(240,160,32,.12); color:var(--warn); border:1px solid rgba(240,160,32,.3); }}
.setups {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
.setup {{ background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:16px 18px; border-top:3px solid var(--line); }}
.setup.buy {{ border-top-color:var(--buy); }} .setup.sell {{ border-top-color:var(--sell); }}
.setup header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }}
.setup .side {{ font-size:20px; font-weight:800; }}
.setup.buy .side {{ color:var(--buy); }} .setup.sell .side {{ color:var(--sell); }}
.setup .side small {{ color:var(--muted); font-size:12px; font-weight:600; margin-left:4px; }}
.tag {{ font-size:11px; padding:3px 9px; border-radius:999px; background:#1b2230; color:var(--muted); text-transform:uppercase; letter-spacing:.06em; }}
.tag-fav {{ background:rgba(227,179,65,.15); color:var(--gold); }}
.tag-ready {{ background:rgba(47,191,113,.18); color:var(--buy); }}
.levels {{ display:grid; gap:6px; }}
.lvl {{ display:flex; justify-content:space-between; padding:7px 10px; border-radius:8px; background:#0d1117; }}
.lvl span {{ color:var(--muted); font-size:13px; }}
.lvl b {{ font-variant-numeric:tabular-nums; }}
.sl {{ color:var(--sell); }} .tp {{ color:var(--buy); }}
.setup footer {{ display:grid; grid-template-columns:repeat(3, auto) 1fr; gap:14px; align-items:center; margin-top:12px; font-size:13px; }}
.setup footer span {{ color:var(--muted); margin-right:4px; }}
.meter {{ position:relative; height:18px; min-width:110px; background:#0d1117; border-radius:6px; overflow:hidden; }}
.meter i {{ position:absolute; inset:0 auto 0 0; background:linear-gradient(90deg, rgba(227,179,65,.35), rgba(227,179,65,.75)); }}
.meter em {{ position:relative; font-style:normal; font-size:11px; padding-left:8px; line-height:18px; color:var(--text); }}
.why {{ margin:10px 0 0; color:var(--muted); font-size:12px; }}
h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.12em; color:var(--muted); margin:30px 0 12px; font-weight:700; }}
.tiles {{ display:grid; grid-template-columns:repeat(4, 1fr); gap:12px; }}
.tile {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:14px 16px; min-height:96px; }}
.tile span {{ display:block; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.08em; }}
.tile b {{ display:block; font-size:22px; margin:4px 0 2px; font-variant-numeric:tabular-nums; }}
.tile small {{ color:var(--muted); font-size:12px; }}
.tile.up b {{ color:var(--buy); }} .tile.down b {{ color:var(--sell); }}
.range {{ display:block; position:relative; height:6px; border-radius:3px; margin-top:8px; background:linear-gradient(90deg, var(--sell), #3a4352, var(--buy)); }}
.range i {{ position:absolute; top:-4px; width:3px; height:14px; background:var(--text); border-radius:2px; }}
.charts {{ display:grid; grid-template-columns:1fr; gap:14px; }}
.panel h3 {{ margin:2px 4px 10px; font-size:13px; color:var(--muted); font-weight:600; }}
.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:14px; }}
.tv {{ height:520px; border-radius:12px; overflow:hidden; }} #tv_gold {{ height:100%; }}
.panel img {{ width:100%; border-radius:10px; display:block; }}
.grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
.events, .headlines, .scores {{ list-style:none; margin:0; padding:0; }}
.events li {{ display:grid; grid-template-columns:auto 1fr auto; gap:4px 10px; padding:10px 0; border-bottom:1px solid var(--line); }}
.events li small {{ grid-column:2 / 4; color:var(--muted); }}
.events time {{ color:var(--muted); font-size:13px; }}
.imp {{ font-size:11px; padding:2px 8px; border-radius:999px; align-self:center; }}
.imp-high {{ background:rgba(239,83,80,.15); color:var(--sell); }} .imp-medium {{ background:rgba(240,160,32,.15); color:var(--warn); }}
.headlines li {{ padding:10px 0; border-bottom:1px solid var(--line); font-size:14px; }}
.hl-tag {{ font-size:11px; color:var(--gold); text-transform:uppercase; letter-spacing:.08em; margin-right:8px; }}
.scores li {{ display:grid; grid-template-columns:1.4fr 1fr auto auto; gap:10px; align-items:center; padding:8px 0; font-size:14px; }}
.scores small {{ color:var(--muted); width:40px; text-align:right; }}
.bar {{ display:block; height:8px; background:#0d1117; border-radius:4px; overflow:hidden; }}
.bar i {{ display:block; height:100%; background:var(--gold); }}
.muted {{ color:var(--muted); }}
details {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:12px 16px; margin-top:14px; }}
summary {{ cursor:pointer; color:var(--muted); font-weight:600; }}
pre {{ white-space:pre-wrap; font:13px/1.55 ui-monospace, Consolas, monospace; color:#c7cdd8; }}
.foot {{ color:var(--muted); font-size:12px; margin-top:30px; text-align:center; }}
@media (max-width: 900px) {{ .hero, .setups, .charts, .grid2 {{ grid-template-columns:1fr; }} .tiles {{ grid-template-columns:repeat(2, 1fr); }} .price {{ font-size:36px; }} }}
</style>
</head>
<body>
<main>
  <div class="top">
    <div>
      <div class="brand">Gold intraday <small>{_e(data['symbol'])} · alerts only</small></div>
      <div class="price">{data['price']:.2f}</div>
    </div>
    <div class="updated">Updated {_e(data['updated'])}<br>Re-analysed every 15 min · price checked every minute</div>
  </div>

  <section class="hero">
    <div class="call {head_class}">
      <div class="label">Current signal</div>
      <div class="big">{_e(head_side)}</div>
      <div class="line">{_e(head_line)}</div>
      {in_zone}
      <p class="stance">{_e(data['stance'])}</p>
      {active_html}
    </div>
    <div class="setups">{setup_cards}</div>
  </section>

  <h2>Market data</h2>
  <div class="tiles">{tiles}</div>

  <h2>Charts</h2>
  <div class="charts">
    <div class="panel"><h3>Live chart · {_e(data['symbol'])} on TradingView</h3>{widget or '<p class="muted">Live chart unavailable.</p>'}</div>
    <div class="panel"><h3>Strategy levels · green band = buy entry, red band = sell entry, solid red = stop, dashed green = target</h3>{plan_image or '<p class="muted">Strategy chart unavailable.</p>'}</div>
  </div>

  <div class="grid2">
    <div>
      <h2>US calendar</h2>
      <div class="panel">{calendar_html}</div>
    </div>
    <div>
      <h2>Headlines behind the signal</h2>
      <div class="panel">{headlines_html}</div>
    </div>
  </div>

  <div class="grid2">
    <div>
      <h2>How each approach scores now</h2>
      <div class="panel"><ul class="scores">{score_rows}</ul></div>
    </div>
    <div>
      <h2>Track record</h2>
      <div class="tiles" style="grid-template-columns:repeat(3,1fr)">{record_html}</div>
      <p class="muted" style="font-size:13px;margin-top:10px">Each plan is graded on the price path after its stop, target, or 120 minutes. The bot trusts approaches that win more on similar days.</p>
    </div>
  </div>

  <details>
    <summary>Full analysis</summary>
    <pre>{_e(brief_text)}</pre>
  </details>

  <p class="foot">Signals are research, not financial advice. No orders are sent. You can lose the amount at risk.</p>
</main>
</body>
</html>
"""
