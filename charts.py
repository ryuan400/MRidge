import html
import json
import re
from datetime import datetime
from pathlib import Path

from engine import calculate_rsi

PERIODS = ("1mo", "3mo", "6mo", "1y")
FETCH_PERIOD = {"1mo": "4mo", "3mo": "6mo", "6mo": "1y", "1y": "2y"}
DISPLAY_DAYS = {"1mo": 22, "3mo": 63, "6mo": 126, "1y": 252}
PERIOD_NAME = {"1mo": "1 month", "3mo": "3 months", "6mo": "6 months", "1y": "1 year"}

W, H = 960, 560
ML, MR, MT = 66, 28, 18
PH, GAP, RH = 340, 30, 120
RT = MT + PH + GAP
PW = W - ML - MR


def _clean(series):
    return [None if v != v else round(float(v), 4) for v in series]


def chart_data(closes, period="6mo"):
    sma = closes.rolling(20).mean()
    rsi = calculate_rsi(closes)
    n = DISPLAY_DAYS.get(period, 126)
    c, s, r = closes.iloc[-n:], sma.iloc[-n:], rsi.iloc[-n:]
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in c.index],
        "close": _clean(c), "sma": _clean(s), "rsi": _clean(r), "period": period,
    }


def summary(data, buy_price=None):
    close = data["close"]
    first, last = close[0], close[-1]
    sma_now = next((v for v in reversed(data["sma"]) if v is not None), None)
    rsi_now = next((v for v in reversed(data["rsi"]) if v is not None), None)
    return {
        "first_price": round(first, 2), "last_price": round(last, 2),
        "change_pct": round((last / first - 1) * 100, 1),
        "high": round(max(close), 2), "low": round(min(close), 2),
        "rsi_now": None if rsi_now is None else round(rsi_now, 1),
        "sma20_now": None if sma_now is None else round(sma_now, 2),
        "above_sma20": None if sma_now is None else last > sma_now,
        "you_paid": buy_price,
    }


def _path(points):
    parts, pen_down = [], False
    for p in points:
        if p is None:
            pen_down = False
            continue
        parts.append(("L" if pen_down else "M") + f"{p[0]:.1f} {p[1]:.1f}")
        pen_down = True
    return " ".join(parts)


def build_chart_html(ticker, data, buy_price=None):
    ticker = re.sub(r"[^A-Za-z0-9\-]", "", ticker).upper()
    dates, close, sma, rsi = data["dates"], data["close"], data["sma"], data["rsi"]
    n = len(dates)
    st = summary(data, buy_price)

    vals = [v for v in close + sma if v is not None]
    if buy_price:
        vals.append(buy_price)
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.07 or 1.0
    lo, hi = lo - pad, hi + pad

    def x_of(i):
        return ML + (i * PW / (n - 1) if n > 1 else 0)

    def y_of(v):
        return MT + (hi - v) / (hi - lo) * PH

    def ry_of(v):
        return RT + (100 - v) / 100 * RH

    up = st["last_price"] >= st["first_price"]
    line_color = "#2fbf8c" if up else "#ff6b81"

    svg = []
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = y_of(v)
        svg.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{W - MR}" y2="{y:.1f}" class="grid"/>')
        svg.append(f'<text x="{ML - 10}" y="{y + 4:.1f}" class="axis" text-anchor="end">${v:,.2f}</text>')
    price_pts = [(x_of(i), y_of(v)) for i, v in enumerate(close)]
    line = _path(price_pts)
    area = f"{line} L{x_of(n - 1):.1f} {MT + PH:.1f} L{x_of(0):.1f} {MT + PH:.1f} Z"
    svg.append(f'<path d="{area}" fill="url(#fade)"/>')
    sma_path = _path([None if v is None else (x_of(i), y_of(v)) for i, v in enumerate(sma)])
    svg.append(f'<path d="{sma_path}" class="sma"/>')
    svg.append(f'<path d="{line}" class="price" stroke="{line_color}"/>')
    if buy_price:
        y = y_of(buy_price)
        svg.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{W - MR}" y2="{y:.1f}" class="paid"/>')
        svg.append(f'<text x="{W - MR - 6}" y="{y - 6:.1f}" class="paidtxt" text-anchor="end">you paid ${buy_price:,.2f}</text>')
    svg.append(f'<rect x="{ML}" y="{ry_of(100):.1f}" width="{PW}" height="{ry_of(70) - ry_of(100):.1f}" class="hot"/>')
    svg.append(f'<rect x="{ML}" y="{ry_of(30):.1f}" width="{PW}" height="{ry_of(0) - ry_of(30):.1f}" class="cold"/>')
    for v in (70, 30):
        svg.append(f'<line x1="{ML}" y1="{ry_of(v):.1f}" x2="{W - MR}" y2="{ry_of(v):.1f}" class="band"/>')
        svg.append(f'<text x="{ML - 10}" y="{ry_of(v) + 4:.1f}" class="axis" text-anchor="end">{v}</text>')
    svg.append(f'<text x="{ML + 6}" y="{RT + 14}" class="axis">RSI</text>')
    rsi_path = _path([None if v is None else (x_of(i), ry_of(v)) for i, v in enumerate(rsi)])
    svg.append(f'<path d="{rsi_path}" class="rsi"/>')
    for k in range(6):
        i = round(k * (n - 1) / 5)
        label = datetime.strptime(dates[i], "%Y-%m-%d").strftime("%b %d")
        anchor = "start" if k == 0 else ("end" if k == 5 else "middle")
        svg.append(f'<text x="{x_of(i):.1f}" y="{RT + RH + 22}" class="axis" text-anchor="{anchor}">{label}</text>')
    svg.append(f'<line id="vl" x1="0" y1="{MT}" x2="0" y2="{RT + RH}" class="vl" style="display:none"/>')
    svg.append('<circle id="dot" r="5" class="dot" style="display:none"/>')
    svg.append(f'<rect id="hit" x="{ML}" y="{MT}" width="{PW}" height="{RT + RH - MT}" fill="transparent"/>')

    js_data = json.dumps({"dates": dates, "close": close, "sma": sma, "rsi": rsi})
    js_geo = json.dumps({"ML": ML, "PW": PW, "MT": MT, "PH": PH, "RT": RT, "RH": RH, "lo": lo, "hi": hi, "W": W})

    def fmt(v, money=True):
        return "n/a" if v is None else (f"${v:,.2f}" if money else f"{v:.1f}")

    sign = "+" if st["change_pct"] >= 0 else ""
    trend_word = "Above" if st["above_sma20"] else "Below"
    paid_stat = f'<div><span>You paid</span>${buy_price:,.2f}</div>' if buy_price else ""
    avg_stat = (f'<div><span>{trend_word} 20-day avg</span>{fmt(st["sma20_now"])}</div>'
                if st["sma20_now"] is not None else "")

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{ticker} chart</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 body {{ margin:0; background:#0f0f14; color:#ececf1; font-family:'Segoe UI', Arial, sans-serif; }}
 .wrap {{ max-width:1000px; margin:0 auto; padding:26px 20px 40px; }}
 h1 {{ margin:0; font-size:30px; }} h1 .p {{ font-weight:400; margin-left:12px; }}
 .chg {{ font-size:15px; margin-left:12px; color:{line_color}; }}
 .sub {{ color:#8b8b9a; font-size:13px; margin:6px 0 18px; }}
 .stats {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px; }}
 .stats div {{ background:#17171f; border-radius:12px; padding:10px 16px; font-size:17px; }}
 .stats span {{ display:block; color:#8b8b9a; font-size:12px; margin-bottom:2px; }}
 .card {{ background:#17171f; border-radius:16px; padding:14px; position:relative; }}
 svg {{ width:100%; height:auto; display:block; }}
 .grid {{ stroke:#2a2a36; stroke-width:1; }}
 .axis {{ fill:#8b8b9a; font-size:12px; }}
 .price {{ fill:none; stroke-width:2.4; stroke-linejoin:round; }}
 .sma {{ fill:none; stroke:#a99bff; stroke-width:1.6; stroke-dasharray:6 5; }}
 .paid {{ stroke:#ffb454; stroke-width:1.4; stroke-dasharray:3 4; }}
 .paidtxt {{ fill:#ffb454; font-size:12px; }}
 .hot {{ fill:rgba(255,107,129,.08); }} .cold {{ fill:rgba(47,191,140,.08); }}
 .band {{ stroke:#3a3a49; stroke-dasharray:3 4; }}
 .rsi {{ fill:none; stroke:#7c5cff; stroke-width:1.8; }}
 .vl {{ stroke:#8b8b9a; stroke-width:1; }} .dot {{ fill:#fff; stroke:{line_color}; stroke-width:2; }}
 #tip {{ position:absolute; pointer-events:none; background:#23232e; border:1px solid #34343f; border-radius:10px;
        padding:8px 12px; font-size:13px; line-height:1.5; display:none; white-space:nowrap; }}
 .legend {{ color:#8b8b9a; font-size:12.5px; margin-top:14px; line-height:1.6; }}
 .legend b {{ color:#ececf1; font-weight:600; }}
</style></head><body><div class="wrap">
<h1>{ticker}<span class="p">${st["last_price"]:,.2f}</span><span class="chg">{sign}{st["change_pct"]:.1f}% over {PERIOD_NAME[data["period"]]}</span></h1>
<div class="sub">Daily closing prices &middot; made {datetime.now().strftime("%b %d, %Y %I:%M %p")}</div>
<div class="stats">
 <div><span>High</span>{fmt(st["high"])}</div><div><span>Low</span>{fmt(st["low"])}</div>
 <div><span>RSI now</span>{fmt(st["rsi_now"], False)}</div>{avg_stat}{paid_stat}
</div>
<div class="card" id="card">
<svg id="c" viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">
<defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="{line_color}" stop-opacity=".28"/><stop offset="1" stop-color="{line_color}" stop-opacity="0"/>
</linearGradient></defs>
{"".join(svg)}
</svg><div id="tip"></div></div>
<div class="legend"><b>Solid line</b> is the price. <b>Dashed purple</b> is the 20-day average the score uses
(price above it counts as a good sign). <b>RSI</b> below the green zone (under 30) means beaten down, and above the
red zone (over 70) means possibly overheated.</div>
</div>
<script>
const D = {js_data}, G = {js_geo};
const svg = document.getElementById('c'), tip = document.getElementById('tip'), card = document.getElementById('card');
const vl = document.getElementById('vl'), dot = document.getElementById('dot'), hit = document.getElementById('hit');
const N = D.dates.length;
const xOf = i => G.ML + (N > 1 ? i * G.PW / (N - 1) : 0);
const yOf = v => G.MT + (G.hi - v) / (G.hi - G.lo) * G.PH;
function hide() {{ vl.style.display = dot.style.display = tip.style.display = 'none'; }}
hit.addEventListener('mouseleave', hide);
hit.addEventListener('mousemove', e => {{
  const r = svg.getBoundingClientRect();
  const px = (e.clientX - r.left) * (G.W / r.width);
  const i = Math.max(0, Math.min(N - 1, Math.round((px - G.ML) / G.PW * (N - 1))));
  const x = xOf(i);
  vl.setAttribute('x1', x); vl.setAttribute('x2', x); vl.style.display = '';
  dot.setAttribute('cx', x); dot.setAttribute('cy', yOf(D.close[i])); dot.style.display = '';
  const d = new Date(D.dates[i] + 'T00:00:00');
  let t = '<b>' + d.toLocaleDateString(undefined, {{month: 'short', day: 'numeric', year: 'numeric'}}) + '</b><br>Close $' + D.close[i].toFixed(2);
  if (D.sma[i] !== null) t += '<br>20-day avg $' + D.sma[i].toFixed(2);
  if (D.rsi[i] !== null) t += '<br>RSI ' + D.rsi[i].toFixed(1);
  tip.innerHTML = t; tip.style.display = 'block';
  const cr = card.getBoundingClientRect();
  let left = e.clientX - cr.left + 16;
  if (left + tip.offsetWidth > cr.width - 8) left = e.clientX - cr.left - tip.offsetWidth - 16;
  tip.style.left = left + 'px'; tip.style.top = (e.clientY - cr.top - 10) + 'px';
}});
</script></body></html>"""


def write_chart_page(path, ticker, data, buy_price=None):
    Path(path).write_text(build_chart_html(ticker, data, buy_price), encoding="utf-8")
    return summary(data, buy_price)
