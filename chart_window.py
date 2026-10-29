import tkinter as tk
from datetime import datetime

import charts

BG, PANEL, TEXT, MUTED = "#0f0f14", "#17171f", "#ececf1", "#8b8b9a"
GRID, BAND, SMA_C, RSI_C, AMBER = "#2a2a36", "#3a3a49", "#a99bff", "#7c5cff", "#ffb454"
GREEN, RED = "#2fbf8c", "#ff6b81"
TIP_BG, TIP_BORDER = "#23232e", "#34343f"
FONT = "Segoe UI"


def mix(fg, bg, a):
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(b[k] + (f[k] - b[k]) * a) for k in range(3))


def _segments(points):
    runs, cur = [], []
    for p in points:
        if p is None:
            if len(cur) > 1:
                runs.append(cur)
            cur = []
        else:
            cur.append(p)
    if len(cur) > 1:
        runs.append(cur)
    return runs


def draw_chart(c, W, H, data, buy_price, line_color):
    close, sma, rsi, dates = data["close"], data["sma"], data["rsi"], data["dates"]
    n = len(close)
    ML, MR, MT, AX, GAP = 72, 24, 14, 28, 26
    avail = max(H - MT - AX - GAP, 120)
    PH = int(avail * 0.68)
    RH = avail - PH
    RT = MT + PH + GAP
    PW = max(W - ML - MR, 50)

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

    for k in range(5):
        v = lo + (hi - lo) * k / 4
        y = y_of(v)
        c.create_line(ML, y, W - MR, y, fill=GRID)
        c.create_text(ML - 8, y, text=f"${v:,.2f}", fill=MUTED, anchor="e", font=(FONT, 9))

    pts = [(x_of(i), y_of(v)) for i, v in enumerate(close)]
    poly = [x_of(0), MT + PH]
    for x, y in pts:
        poly += [x, y]
    poly += [x_of(n - 1), MT + PH]
    c.create_polygon(*poly, fill=mix(line_color, PANEL, 0.14), outline="")
    sma_pts = [None if v is None else (x_of(i), y_of(v)) for i, v in enumerate(sma)]
    for run in _segments(sma_pts):
        c.create_line(*[q for p in run for q in p], fill=SMA_C, width=1.6, dash=(6, 5))
    c.create_line(*[q for p in pts for q in p], fill=line_color, width=2.4, joinstyle="round")

    if buy_price:
        y = y_of(buy_price)
        c.create_line(ML, y, W - MR, y, fill=AMBER, width=1.4, dash=(3, 4))
        c.create_text(W - MR - 6, y - 8, text=f"you paid ${buy_price:,.2f}", fill=AMBER, anchor="e", font=(FONT, 9))

    c.create_rectangle(ML, ry_of(100), W - MR, ry_of(70), fill=mix(RED, PANEL, 0.10), outline="")
    c.create_rectangle(ML, ry_of(30), W - MR, ry_of(0), fill=mix(GREEN, PANEL, 0.10), outline="")
    for v in (70, 30):
        c.create_line(ML, ry_of(v), W - MR, ry_of(v), fill=BAND, dash=(3, 4))
        c.create_text(ML - 8, ry_of(v), text=str(v), fill=MUTED, anchor="e", font=(FONT, 9))
    c.create_text(ML + 6, RT + 10, text="RSI", fill=MUTED, anchor="w", font=(FONT, 9))
    rsi_pts = [None if v is None else (x_of(i), ry_of(v)) for i, v in enumerate(rsi)]
    for run in _segments(rsi_pts):
        c.create_line(*[q for p in run for q in p], fill=RSI_C, width=1.8)

    for k in range(6):
        i = round(k * (n - 1) / 5)
        label = datetime.strptime(dates[i], "%Y-%m-%d").strftime("%b %d")
        anchor = "w" if k == 0 else ("e" if k == 5 else "center")
        c.create_text(x_of(i), RT + RH + 14, text=label, fill=MUTED, anchor=anchor, font=(FONT, 9))

    return {"ML": ML, "MR": MR, "PW": PW, "MT": MT, "RT": RT, "RH": RH, "W": W, "n": n, "x_of": x_of, "y_of": y_of}


class ChartWindow(tk.Toplevel):
    def __init__(self, master, ticker, data, buy_price=None, on_browser=None):
        super().__init__(master)
        self.data, self.buy_price, self.g = data, buy_price, None
        st = charts.summary(data, buy_price)
        up = st["last_price"] >= st["first_price"]
        self.line_color = GREEN if up else RED
        self.title(f"MRidge - {ticker} chart")
        self.geometry("1000x720+150+70")
        self.minsize(560, 460)
        self.configure(bg=BG)

        head = tk.Frame(self, bg=BG)
        head.pack(fill="x", padx=18, pady=(14, 2))
        tk.Label(head, text=ticker, bg=BG, fg=TEXT, font=(FONT, 22, "bold")).pack(side="left")
        tk.Label(head, text=f"${st['last_price']:,.2f}", bg=BG, fg=TEXT, font=(FONT, 20)).pack(side="left", padx=(12, 0))
        sign = "+" if st["change_pct"] >= 0 else ""
        tk.Label(head, text=f"{sign}{st['change_pct']:.1f}% over {charts.PERIOD_NAME[data['period']]}",
                 bg=BG, fg=self.line_color, font=(FONT, 12)).pack(side="left", padx=(12, 0), pady=(6, 0))
        if on_browser:
            tk.Button(head, text="Open in browser", command=on_browser, bg="#2c2c38", fg=TEXT, relief="flat",
                      activebackground="#3a3a49", activeforeground=TEXT, font=(FONT, 10), padx=12, pady=3,
                      cursor="hand2").pack(side="right")

        def fmt(v, money=True):
            return "n/a" if v is None else (f"${v:,.2f}" if money else f"{v:.1f}")

        cards = [("High", fmt(st["high"])), ("Low", fmt(st["low"])), ("RSI now", fmt(st["rsi_now"], False))]
        if st["sma20_now"] is not None:
            cards.append((("Above" if st["above_sma20"] else "Below") + " 20-day avg", fmt(st["sma20_now"])))
        if buy_price:
            cards.append(("You paid", f"${buy_price:,.2f}"))
        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=18, pady=(6, 10))
        for label, value in cards:
            box = tk.Frame(row, bg=PANEL)
            box.pack(side="left", padx=(0, 10))
            tk.Label(box, text=label, bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=14, pady=(8, 0))
            tk.Label(box, text=value, bg=PANEL, fg=TEXT, font=(FONT, 14)).pack(anchor="w", padx=14, pady=(0, 8))

        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=18)
        tk.Label(self, justify="left", anchor="w", bg=BG, fg=MUTED, font=(FONT, 9), wraplength=950,
                 text="Solid line = price.  Dashed purple = the 20-day average the score uses (price above it counts as a good "
                      "sign).  RSI under 30 (green zone) means beaten down, over 70 (red zone) means possibly overheated."
                 ).pack(fill="x", padx=18, pady=(8, 14))

        self._job = None
        self.canvas.bind("<Configure>", lambda e: self._later())
        self.canvas.bind("<Motion>", self.hover)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("hover"))
        self.lift()
        self.focus_force()

    def _later(self):
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(40, self.redraw)

    def redraw(self):
        self._job = None
        self.canvas.delete("all")
        W, H = self.canvas.winfo_width(), self.canvas.winfo_height()
        if W < 100 or H < 100:
            return
        self.g = draw_chart(self.canvas, W, H, self.data, self.buy_price, self.line_color)

    def hover(self, e):
        g, c, d = self.g, self.canvas, self.data
        c.delete("hover")
        if not g or g["n"] < 1:
            return
        n = g["n"]
        i = max(0, min(n - 1, round((e.x - g["ML"]) / g["PW"] * (n - 1)))) if n > 1 else 0
        x, y = g["x_of"](i), g["y_of"](d["close"][i])
        c.create_line(x, g["MT"], x, g["RT"] + g["RH"], fill=MUTED, tags="hover")
        c.create_oval(x - 5, y - 5, x + 5, y + 5, fill="#ffffff", outline=self.line_color, width=2, tags="hover")
        day = datetime.strptime(d["dates"][i], "%Y-%m-%d").strftime("%b %d, %Y")
        lines = [day, f"Close  ${d['close'][i]:,.2f}"]
        if d["sma"][i] is not None:
            lines.append(f"20-day avg  ${d['sma'][i]:,.2f}")
        if d["rsi"][i] is not None:
            lines.append(f"RSI  {d['rsi'][i]:.1f}")
        text = "\n".join(lines)
        t = c.create_text(0, 0, text=text, fill=TEXT, anchor="nw", font=(FONT, 10), tags="hover")
        x1, y1, x2, y2 = c.bbox(t)
        w, h = x2 - x1 + 20, y2 - y1 + 16
        left = x + 16 if x + 16 + w < g["W"] - 6 else x - 16 - w
        top = max(6, min(y - 10, c.winfo_height() - h - 6))
        box = c.create_rectangle(left, top, left + w, top + h, fill=TIP_BG, outline=TIP_BORDER, tags="hover")
        c.coords(t, left + 10, top + 8)
        c.tag_raise(t, box)
