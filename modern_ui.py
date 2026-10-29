import json
import queue
import threading
from tkinter import ttk

import customtkinter as ctk

import portfolio

ACCENT = "#7c5cff"
ACCENT_HOVER = "#9179ff"
ACCENT_TEXT = "#a99bff"
BG = "#0f0f14"
PANEL = "#17171f"
BOT_BUBBLE = "#23232e"
INPUT_BG = "#1d1d27"
TEXT = "#ececf1"
MUTED = "#8b8b9a"
GREEN = "#2fbf8c"
GREEN_HOVER = "#3fd4a0"
GRAY_BTN = "#2c2c38"
GRAY_HOVER = "#3a3a49"
RED = "#ff6b81"
AMBER = "#ffb454"
FONT = "Segoe UI"


def money(v):
    return f"${v:,.2f}"


def signed_pct(v):
    return f"{round(v, 1) + 0.0:+.1f}%"


def plain_pct(v):
    return f"{v:.1f}%"


def score(v):
    return f"{v:+.2f}"


def raw2(v):
    return f"{v:.2f}"


def raw_signed1(v):
    return f"{round(v, 1) + 0.0:+.1f}"


def raw1(v):
    return f"{v:.1f}"


REC_COLS = [
    ("ticker", "Ticker", 90, "w", str, str),
    ("shares", "Shares", 80, "e", str, str),
    ("price", "Price", 110, "e", money, raw2),
    ("cost", "Cost", 120, "e", money, raw2),
    ("score", "Score", 80, "e", score, score),
    ("pct", "% of money", 110, "e", plain_pct, raw1),
]
PORT_COLS = [
    ("ticker", "Ticker", 80, "w", str, str),
    ("shares", "Shares", 70, "e", str, str),
    ("avg_cost", "Bought at", 110, "e", money, raw2),
    ("price", "Now", 110, "e", money, raw2),
    ("pl_pct", "Profit/Loss", 110, "e", signed_pct, raw_signed1),
    ("score", "Score", 70, "e", score, score),
    ("verdict", "Verdict", 110, "center", str, str),
]
REVIEW_COLORS = {"SELL": RED, "SWAP OUT": AMBER, "KEEP": TEXT, "BUY MORE": GREEN_HOVER}
AFTER_COLORS = {"NEW": GREEN_HOVER, "ADDED": GREEN_HOVER, "KEEP": TEXT}


class TableWindow(ctk.CTkToplevel):
    def __init__(self, master, title, geometry, columns, tag_colors, on_headlines,
                 on_save=None, save_text="", saved_text=""):
        super().__init__(master)
        self.title(title)
        self.geometry(geometry)
        self.configure(fg_color=BG)
        self.columns = columns
        self.tag_colors = tag_colors
        self.rows = []
        self.sort_col = None
        self.sort_rev = False
        self.save_text = save_text
        self.saved_text = saved_text

        self.header = ctk.CTkLabel(self, text="", font=(FONT, 22, "bold"), text_color=TEXT, anchor="w")
        self.header.pack(fill="x", padx=22, pady=(20, 0))
        self.summary = ctk.CTkLabel(self, text="", font=(FONT, 13), text_color=MUTED, anchor="w", justify="left")
        self.summary.pack(fill="x", padx=22, pady=(4, 12))

        card = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=16)
        card.pack(fill="both", expand=True, padx=22)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=8, pady=8)

        self._style_tree()
        self.tree = ttk.Treeview(inner, columns=[c[0] for c in columns], show="headings",
                                 selectmode="browse", style="MR.Treeview")
        for key, label, width, anchor, _show, _copy in columns:
            self.tree.heading(key, text=label, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, anchor=anchor)
        if tag_colors:
            for tag, color in tag_colors.items():
                self.tree.tag_configure(tag, foreground=color)
        bar_scroll = ctk.CTkScrollbar(inner, command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        bar_scroll.pack(side="right", fill="y")

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=22, pady=16)
        ctk.CTkButton(bar, text="Open headlines in browser", command=on_headlines, corner_radius=12, height=38,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, font=(FONT, 13, "bold")).pack(side="left")
        ctk.CTkButton(bar, text="Copy table", command=self.copy_table, corner_radius=12, height=38,
                      fg_color=GRAY_BTN, hover_color=GRAY_HOVER, font=(FONT, 13)).pack(side="left", padx=10)
        self.save_btn = None
        if on_save:
            self.save_btn = ctk.CTkButton(bar, text=save_text, command=on_save, corner_radius=12, height=38,
                                          fg_color=GREEN, hover_color=GREEN_HOVER, font=(FONT, 13, "bold"))
            self.save_btn.pack(side="left")
        self.hint = ctk.CTkLabel(bar, text="", font=(FONT, 12), text_color=MUTED)
        self.hint.pack(side="right")

    def _style_tree(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("MR.Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                        rowheight=36, borderwidth=0, font=(FONT, 11))
        style.configure("MR.Treeview.Heading", background=BOT_BUBBLE, foreground=TEXT, relief="flat",
                        font=(FONT, 11, "bold"), padding=(6, 8))
        style.map("MR.Treeview", background=[("selected", ACCENT)], foreground=[("selected", "white")])
        style.map("MR.Treeview.Heading", background=[("active", GRAY_HOVER)])
        style.layout("MR.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    def show(self, header, summary, rows):
        self.rows = list(rows)
        self.sort_col = None
        self.header.configure(text=header)
        self.summary.configure(text=summary)
        if self.save_btn:
            self.save_btn.configure(text=self.save_text, state="normal", fg_color=GREEN)
        self.refresh()
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.rows:
            values = [c[4](r[c[0]]) for c in self.columns]
            tags = (r["verdict"],) if self.tag_colors and "verdict" in r else ()
            self.tree.insert("", "end", values=values, tags=tags)

    def sort_by(self, col):
        first_is_text = col in ("ticker", "verdict")
        self.sort_rev = (not self.sort_rev) if self.sort_col == col else (not first_is_text)
        self.sort_col = col
        self.rows.sort(key=lambda r: r[col], reverse=self.sort_rev)
        self.refresh()

    def copy_table(self):
        lines = ["\t".join(c[1] for c in self.columns)]
        for r in self.rows:
            lines.append("\t".join(c[5](r[c[0]]) for c in self.columns))
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.hint.configure(text="copied!")
        self.after(2000, self._clear_hint)

    def _clear_hint(self):
        try:
            self.hint.configure(text="")
        except Exception:
            pass

    def mark_saved(self):
        if self.save_btn:
            self.save_btn.configure(text=self.saved_text, state="disabled", fg_color=GRAY_BTN)


class ModernApp:
    def __init__(self, root, core):
        self.root = root
        self.core = core
        root.title("MRidge")
        root.geometry("940x760")
        root.minsize(560, 480)
        root.configure(fg_color=BG)

        self.msgs = queue.Queue()
        self.busy = False
        self.results_win = None
        self.portfolio_win = None
        self.swapped_win = None
        self.labels = []
        self.typing = None
        self.typing_label = None
        self.wrap = 560
        self._resize_job = None

        reset_note = portfolio.auto_reset(core.EXPERIMENT_START)
        self.assistant = core.Assistant(emit=lambda kind, payload: self.msgs.put((kind, payload)))

        self.build_ui()
        if reset_note:
            self.add_status(reset_note)
        if core.load_api_key():
            self.add_message("bot", "yo! I'm MRidge. tell me how much you wanna invest and I'll figure out how to "
                                    "split it up across S&P 500 stocks. ask me anything after, like why I picked "
                                    "something, or to pull up the headlines. once you buy, I can track your stocks "
                                    "and tell you tomorrow whether to keep, sell or buy more.")
        else:
            self.add_status("No API key found. Create a file called api_key.txt in this folder, paste your "
                            "Anthropic API key in it, save, and restart the app.")
        root.bind("<Configure>", self.on_resize)
        root.after(100, self.poll)
        self.assistant.show_saved_review()

    def build_ui(self):
        header = ctk.CTkFrame(self.root, fg_color=PANEL, corner_radius=0, height=64)
        header.pack(fill="x")
        header.pack_propagate(False)
        ctk.CTkLabel(header, text="MRidge", font=(FONT, 22, "bold"), text_color=TEXT).pack(side="left", padx=(22, 10))
        self.toast = ctk.CTkLabel(header, text="", font=(FONT, 12), text_color=ACCENT_TEXT)
        self.toast.pack(side="right", padx=22)

        self.chat = ctk.CTkScrollableFrame(self.root, fg_color=BG, corner_radius=0)
        self.chat.pack(fill="both", expand=True)

        bottom = ctk.CTkFrame(self.root, fg_color=PANEL, corner_radius=0)
        bottom.pack(fill="x")
        self.entry = ctk.CTkEntry(bottom, placeholder_text="Message MRidge...", height=46, corner_radius=23,
                                  border_width=0, fg_color=INPUT_BG, text_color=TEXT, font=(FONT, 14))
        self.entry.pack(side="left", fill="x", expand=True, padx=(18, 10), pady=14)
        self.entry.bind("<Return>", lambda e: self.on_send())
        self.send_btn = ctk.CTkButton(bottom, text="Send", width=90, height=46, corner_radius=23,
                                      fg_color=ACCENT, hover_color=ACCENT_HOVER, font=(FONT, 14, "bold"),
                                      command=self.on_send)
        self.send_btn.pack(side="right", padx=(0, 18), pady=14)
        self.entry.focus()

    def add_message(self, kind, text):
        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=5)
        if kind == "you":
            bubble = ctk.CTkFrame(row, fg_color=ACCENT, corner_radius=20)
            bubble.pack(side="right")
            color, top_pad = "white", 10
        else:
            bubble = ctk.CTkFrame(row, fg_color=BOT_BUBBLE, corner_radius=20)
            bubble.pack(side="left")
            ctk.CTkLabel(bubble, text="MRidge", font=(FONT, 12, "bold"), text_color=ACCENT_TEXT,
                         anchor="w").pack(anchor="w", padx=16, pady=(10, 0))
            color, top_pad = TEXT, 2
        label = ctk.CTkLabel(bubble, text=text, wraplength=self.wrap, justify="left", anchor="w",
                             font=(FONT, 14), text_color=color)
        label.pack(padx=16, pady=(top_pad, 10))
        label.bind("<Button-3>", lambda e, t=text: self.copy_text(t))
        self.labels.append(label)
        self.scroll_down()

    def add_status(self, text):
        label = ctk.CTkLabel(self.chat, text=text, font=(FONT, 12), text_color=MUTED,
                             wraplength=self.wrap, justify="center")
        label.pack(fill="x", padx=14, pady=1)
        self.labels.append(label)
        self.scroll_down()

    def show_typing(self):
        self.hide_typing()
        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=5)
        bubble = ctk.CTkFrame(row, fg_color=BOT_BUBBLE, corner_radius=20)
        bubble.pack(side="left")
        self.typing_label = ctk.CTkLabel(bubble, text="MRidge is thinking", font=(FONT, 13, "italic"),
                                         text_color=MUTED)
        self.typing_label.pack(padx=16, pady=10)
        self.typing = row
        self.scroll_down()
        self.animate_typing(0)

    def animate_typing(self, n):
        if self.typing is None:
            return
        try:
            self.typing_label.configure(text="MRidge is thinking" + "." * (n % 4))
        except Exception:
            return
        self.root.after(400, lambda: self.animate_typing(n + 1))

    def hide_typing(self):
        if self.typing is not None:
            try:
                self.typing.destroy()
            except Exception:
                pass
            self.typing = None
            self.typing_label = None

    def scroll_down(self):
        def go():
            try:
                self.root.update_idletasks()
                self.chat._parent_canvas.yview_moveto(1.0)
            except Exception:
                pass
        self.root.after(60, go)

    def on_resize(self, event):
        if event.widget is not self.root:
            return
        if self._resize_job:
            self.root.after_cancel(self._resize_job)
        self._resize_job = self.root.after(120, self.apply_wrap)

    def apply_wrap(self):
        self.wrap = max(260, int(self.root.winfo_width() * 0.62))
        for label in list(self.labels):
            try:
                label.configure(wraplength=self.wrap)
            except Exception:
                self.labels.remove(label)

    def copy_text(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.toast.configure(text="copied message")
        self.root.after(1500, lambda: self.toast.configure(text=""))

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.entry.configure(state=state)
        self.send_btn.configure(state=state)
        if not busy:
            self.entry.focus()

    def on_send(self):
        text = self.entry.get().strip()
        if not text or self.busy:
            return
        if not self.core.load_api_key():
            self.add_status("No API key yet. Put it in api_key.txt and restart the app.")
            return
        self.entry.delete(0, "end")
        self.add_message("you", text)
        self.show_typing()
        self.set_busy(True)
        threading.Thread(target=self.worker, args=(text,), daemon=True).start()

    def worker(self, text):
        try:
            self.assistant.handle(text)
        finally:
            self.msgs.put(("done", ""))

    def poll(self):
        try:
            while True:
                kind, payload = self.msgs.get_nowait()
                try:
                    self.dispatch(kind, payload)
                except Exception as e:
                    self.add_status(f"(display problem: {e})")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def dispatch(self, kind, payload):
        if kind == "done":
            self.hide_typing()
            self.set_busy(False)
        elif kind == "bot":
            self.hide_typing()
            self.add_message("bot", payload)
        elif kind == "status":
            self.add_status(payload)
        elif kind == "results":
            self.show_results(payload)
        elif kind == "portfolio":
            self.show_portfolio(payload)
        elif kind == "portfolio_after":
            self.show_swapped(payload)
        elif kind == "chart":
            self.show_chart(payload)

    @staticmethod
    def alive(win):
        try:
            return win is not None and bool(win.winfo_exists())
        except Exception:
            return False

    def show_results(self, data):
        if not self.alive(self.results_win):
            self.results_win = TableWindow(
                self.root, "MRidge - your split", "720x580+60+50", REC_COLS, None,
                on_headlines=lambda: self.open_headlines(
                    [p["ticker"] for p in (self.assistant.last or {}).get("picks", [])]),
                on_save=self.save_clicked, save_text="I bought these (save)", saved_text="Saved to portfolio")
        summary = (f"Invested: {money(data['invested'])}    Cash left: {money(data['cash'])}\n"
                   f"Based on scan from {data['scan_time']}")
        self.results_win.show(f"Your ${data['amount']:,.0f} split", summary, data["rows"])

    def show_portfolio(self, data):
        if not self.alive(self.portfolio_win):
            self.portfolio_win = TableWindow(
                self.root, "MRidge - portfolio review", "840x600+40+40", PORT_COLS, REVIEW_COLORS,
                on_headlines=lambda: self.open_headlines(list(self.assistant.review_scores)))
        sign = "+" if data["total_pl"] >= 0 else "-"
        text = (f"Worth now if you swap nothing: {money(data['total_value'])}    Paid: {money(data['total_cost'])}    "
                f"Total: {sign}${abs(data['total_pl']):,.2f} ({signed_pct(data['total_pl_pct'])})\n"
                f"Cash: {money(data.get('cash', 0.0))}    Fresh check: {data['when']}")
        for sw in data.get("swaps", []):
            w, b = sw["sell"], sw["buy"]
            text += (f"\nSwap idea: sell {w['ticker']} ({w['shares']} sh, ${w['proceeds']:,.0f}) "
                     f"-> buy {b['ticker']} ({b['shares']} sh)")
        if data["missing"]:
            text += "\nNo data for: " + ", ".join(data["missing"])
        self.portfolio_win.show("Your portfolio review", text, data["rows"])
        if not data.get("swaps") and self.alive(self.swapped_win):
            self.swapped_win.destroy()
            self.swapped_win = None

    def show_chart(self, p):
        import chart_window
        chart_window.ChartWindow(self.root, p["ticker"], p["data"], p["buy_price"],
                                 on_browser=lambda: self.assistant.open_page(p["html_path"]))

    def show_swapped(self, data):
        if not self.alive(self.swapped_win):
            self.swapped_win = TableWindow(
                self.root, "MRidge - portfolio after swaps", "840x640+170+110", PORT_COLS, AFTER_COLORS,
                on_headlines=lambda: self.open_headlines([r["ticker"] for r in self.swapped_win.rows]),
                on_save=self.apply_swaps_clicked, save_text="I made these swaps (save)", saved_text="Swaps saved")
        text = (f"Worth after swaps: {money(data['total_after'])}  (stocks {money(data['stocks_value'])} "
                f"+ cash {money(data['cash'])})    Before: {money(data['total_before'])}")
        for sw in data["swaps"]:
            w, b = sw["sell"], sw["buy"]
            text += f"\nSell {w['ticker']} ({w['shares']} sh)  ->  buy {b['ticker']} ({b['shares']} sh)"
        text += "\nClick the green button once you've made these trades, so I remember them."
        self.swapped_win.show("Your portfolio after the swaps", text, data["rows"])

    def open_headlines(self, tickers):
        try:
            result = self.assistant.tool_open_headlines(tickers or None)
            if '"error"' in result:
                self.add_status("Couldn't open headlines: " + json.loads(result)["error"])
        except Exception as e:
            self.add_status(f"Couldn't open headlines: {e}")

    def save_clicked(self):
        try:
            result = json.loads(self.assistant.tool_record_purchases(use_latest=True))
        except Exception as e:
            self.add_status(f"Couldn't save: {e}")
            return
        if "error" in result:
            self.add_status("Couldn't save: " + result["error"])
        else:
            self.add_status(f"Saved {len(result['saved'])} stocks to your portfolio. Tomorrow, ask me whether "
                            f"to keep, sell or buy more.")
            self.results_win.mark_saved()

    def apply_swaps_clicked(self):
        try:
            result = json.loads(self.assistant.tool_apply_swaps())
        except Exception as e:
            self.add_status(f"Couldn't save the swaps: {e}")
            return
        if "error" in result:
            self.add_status("Couldn't save the swaps: " + result["error"])
            return
        self.add_status(f"Saved {len(result['swapped'])} swap(s). Your portfolio and cash are updated "
                        f"(cash now ${result['cash_now']:,.2f}).")
        self.swapped_win.mark_saved()


def run(core):
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    ModernApp(root, core)
    root.mainloop()
