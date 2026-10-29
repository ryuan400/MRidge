import copy
import html
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import scrolledtext, ttk
from urllib.parse import quote_plus

import anthropic

import charts
import engine as eng
import portfolio
import review
from review import apply_swaps_to_portfolio
from engine import Engine, MAX_SWAPS, NEWS_CANDIDATES, SELL_BELOW, SWAP_GAP, THRESHOLD

MODEL = "claude-sonnet-5-5"

USE_MODERN_UI = True

EXPERIMENT_START = "2026-10-19"

HERE = Path(__file__).resolve().parent
REPORT_FILE = HERE / "headlines_report.html"


def build_headlines_page(scores, tickers, path, title="Headlines used for the picks"):
    sections = []
    total = 0
    for t in tickers:
        d = scores.get(t)
        if not d:
            continue
        items = []
        for h in d["headlines"]:
            link = h["link"] or ("https://www.google.com/search?q=" + quote_plus(h["title"]))
            label = h["label"].lower()
            pub = html.escape(h["publisher"]) if h["publisher"] else ""
            items.append(
                f'<li><span class="badge {html.escape(label)}">{html.escape(label)} {h["score"]:+.2f}</span> '
                f'<a href="{html.escape(link, quote=True)}" target="_blank" rel="noopener">{html.escape(h["title"])}</a> '
                f'<span class="pub">{pub}</span></li>')
            total += 1
        if not items:
            items = ['<li class="pub">No headlines found for this one.</li>']
        sections.append(
            f'<section data-t="{html.escape(t)}"><h2>{html.escape(t)} <span class="mood">news mood {d["sentiment"]:+.2f} '
            f'&middot; overall score {d["composite"]:+.2f}</span></h2><ul>{"".join(items)}</ul></section>')

    page = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Headlines used</title>
<style>
 body {{ font-family: Segoe UI, Arial, sans-serif; background:#1e1e1e; color:#e6e6e6; max-width:900px; margin:30px auto; padding:0 16px; }}
 h1 {{ font-size:22px; }} h2 {{ margin-top:28px; border-bottom:1px solid #444; padding-bottom:6px; }}
 .mood {{ font-size:13px; color:#9a9a9a; font-weight:normal; }}
 ul {{ list-style:none; padding:0; }} li {{ margin:10px 0; line-height:1.4; }}
 a {{ color:#7fc8ff; text-decoration:none; }} a:hover {{ text-decoration:underline; }}
 .pub {{ color:#8a8a8a; font-size:12px; }}
 .badge {{ display:inline-block; min-width:92px; text-align:center; font-size:11px; padding:2px 6px; border-radius:4px; margin-right:6px; }}
 #q {{ width:100%; box-sizing:border-box; padding:10px 14px; font-size:15px; border-radius:8px; border:1px solid #444; background:#2a2a2a; color:#e6e6e6; margin:8px 0 4px; }}
 .positive {{ background:#1f5f3a; }} .negative {{ background:#7a2a2a; }} .neutral {{ background:#444; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="pub">Generated {datetime.now().strftime("%b %d, %Y %I:%M %p")} &middot; {total} headlines.
Labels come from the FinBERT sentiment model. {len(sections)} stocks, best overall score first.</p>
<input id="q" type="search" placeholder="Type a ticker (like NVDA) or a word from a headline to filter..." autofocus>
<p id="count" class="pub"></p>
{"".join(sections)}
<script>
const q = document.getElementById('q'), cnt = document.getElementById('count');
const secs = Array.from(document.querySelectorAll('section'));
function filt() {{
  const v = q.value.trim().toLowerCase(); let n = 0;
  secs.forEach(s => {{ const ok = !v || s.dataset.t.toLowerCase().startsWith(v) || s.textContent.toLowerCase().includes(v); s.style.display = ok ? '' : 'none'; if (ok) n++; }});
  cnt.textContent = v ? n + ' of ' + secs.length + ' stocks match' : '';
}}
q.addEventListener('input', filt);
</script>
</body></html>"""
    Path(path).write_text(page, encoding="utf-8")
    return total


def open_in_browser(path):
    url = Path(path).resolve().as_uri()
    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in chrome_paths:
        if os.path.exists(c):
            webbrowser.register("chrome-custom", None, webbrowser.BackgroundBrowser(c))
            webbrowser.get("chrome-custom").open(url)
            return "Chrome"
    webbrowser.open(url)
    return "the default browser"


TOOLS = [
    {
        "name": "get_recommendation",
        "description": (
            "Scan the S&P 500 (uses a saved scan if recent) and work out how to split a dollar "
            "amount across stocks. The app itself opens a results window with the allocation table. "
            "Returns each pick's shares, cost, weight, scores and sample headlines. "
            "Can take several minutes the first time."),
        "input_schema": {
            "type": "object",
            "properties": {"amount": {"type": "number", "description": "Dollars the user wants to invest, e.g. 10000"}},
            "required": ["amount"],
        },
    },
    {
        "name": "open_headlines",
        "description": (
            "Open a web page in the user's browser (Chrome if available) listing the actual news headlines "
            "used for scoring, with sentiment labels and clickable links. By default shows every stock in "
            "the latest recommendation; pass tickers to show only specific ones, or set all_stocks to true to show every "
            "stock that was news-scanned (hundreds, with a search box on the page)."),
        "input_schema": {
            "type": "object",
            "properties": {"tickers": {"type": "array", "items": {"type": "string"},
                                       "description": "Optional list of ticker symbols, e.g. [\"NVDA\", \"AAPL\"]"},
                           "all_stocks": {"type": "boolean",
                                          "description": "True to open the headlines for every scanned stock, best score first."}},
        },
    },
    {
        "name": "get_stock_details",
        "description": (
            "Get the full scoring breakdown and up to 10 headlines for one ticker from the latest scan. "
            "Works for any stock that was news-scanned, even if it wasn't picked."),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Ticker symbol, e.g. NVDA"}},
            "required": ["ticker"],
        },
    },
    {
        "name": "record_purchases",
        "description": (
            "Save stocks the user bought into their portfolio file so they are remembered tomorrow. "
            "Use use_latest_recommendation=true when they bought the stocks from the latest recommendation "
            "(it saves those shares at the scanned prices). Use purchases for anything else, e.g. different "
            "amounts or prices. price is the price paid per share and is optional."),
        "input_schema": {
            "type": "object",
            "properties": {
                "use_latest_recommendation": {"type": "boolean"},
                "purchases": {"type": "array", "items": {"type": "object", "properties": {
                    "ticker": {"type": "string"}, "shares": {"type": "integer"},
                    "price": {"type": "number"}}, "required": ["ticker", "shares"]}},
            },
        },
    },
    {
        "name": "record_sale",
        "description": "Record that the user sold a stock they own. Leave shares out to remove the whole position. The sale proceeds are added to the portfolio's cash.",
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string"}, "shares": {"type": "integer"},
                           "price": {"type": "number", "description": "Price per share it sold for. Optional; the latest known price is used if left out."}},
            "required": ["ticker"],
        },
    },
    {
        "name": "apply_swaps",
        "description": (
            "Record ALL of the swaps from the latest portfolio review as done: sells the weak stocks and buys the strong "
            "ones in the user's tracked portfolio at the review's prices. Use when the user says they made the suggested "
            "swaps. If they only did some, or did something different, use record_sale and record_purchases instead."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "set_cash",
        "description": "Set how much uninvested cash the portfolio has, when the user tells you (for example 'I also have $500 cash').",
        "input_schema": {"type": "object", "properties": {"amount": {"type": "number"}}, "required": ["amount"]},
    },
    {
        "name": "get_portfolio",
        "description": (
            "Show what the user currently owns with current prices and profit/loss. Quick, no news scoring. "
            "Use for 'what do I own' or 'how am I doing'."),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "review_portfolio",
        "description": (
            "Re-score every stock the user owns with fresh prices and news, and decide SELL, KEEP or BUY MORE "
            "for each one. Opens a review window. Takes a minute or two. Use whenever the user asks whether to "
            "buy, sell, keep or hold what they own. It can also suggest swaps: selling the weakest holding to pay "
            "for a clearly stronger stock. Set include_new_ideas to true to scan the whole market first (slow, "
            "several minutes) so swaps can target stocks the user doesn't own yet; otherwise it only uses a recent "
            "saved scan if there is one."),
        "input_schema": {"type": "object", "properties": {"include_new_ideas": {"type": "boolean"}}},
    },
    {
        "name": "show_chart",
        "description": (
            "Open a price chart window for one stock: price line, the 20-day average, RSI underneath, "
            "and a line for what the user paid if they own it. Works for any ticker, not just scanned ones. "
            "period is one of 1mo, 3mo, 6mo (default) or 1y."),
        "input_schema": {
            "type": "object",
            "properties": {"ticker": {"type": "string", "description": "Ticker symbol, e.g. NVDA"},
                           "period": {"type": "string", "enum": ["1mo", "3mo", "6mo", "1y"]}},
            "required": ["ticker"],
        },
    },
    {
        "name": "refresh_scan",
        "description": "Throw away the saved scan and re-scan the market with fresh prices and news. Slow (minutes).",
        "input_schema": {"type": "object", "properties": {}},
    },
]

NEWS_NOTE = ("every stock in the scan gets its headlines read" if NEWS_CANDIDATES is None
             else f"only the top {NEWS_CANDIDATES} stocks by price signals get their news read")

SYSTEM_PROMPT = f"""You are MRidge, a laid-back, friendly stock-picking buddy inside a desktop chat app. If asked who you are, you are MRidge. Talk like a normal person: casual, short, no corporate tone. The user is a beginner, so explain terms like RSI or "20-day average" in plain words when they come up.

You have tools backed by a real scoring system built by the user. It looks at S&P 500 stocks and gives each a technical score (is the price above its 20-day average, and is RSI oversold/overbought) and a news score (a finance sentiment model reads recent headlines). Composite = 0.6 x technical + 0.4 x news. It only buys stocks with a composite above {THRESHOLD}, and splits the money in proportion to each stock's score.

How the money gets split (this is exactly what the code does, so use it when explaining share counts):
1. Every stock above the threshold gets a target of: amount x (its score / sum of all picked scores).
2. Proportional step: it buys as many WHOLE shares as fit in that target. A pricey stock whose target is less than one share price gets 0 shares here.
3. Leftover-cash step: the cash left over is spent one share at a time, going down the list from the highest score, round after round, as long as it can afford a share. A stock can get shares here that its target alone would not have paid for, so its real share of the money can end up bigger than its target.
So share counts are NOT a measure of how much the scorer likes a stock: cheap stocks naturally get lots of shares and expensive ones get few. Each pick's data includes target_dollars, shares_from_proportional_step and shares_from_leftover_cash_step so you can say exactly where its shares came from.

How to explain things (very important): keep it short, casual and easy for anyone to read. When the user asks WHY about a stock (why you picked it, why you skipped it, why it got a big or small share), explain it with whatever reasons actually fit, each as a quick casual verdict, like "the RSI is a bit high", "the headlines aren't looking too good", "price trend isn't that great". RSI, headlines and price trend are the usual ones, but use any other reason the data really supports, in the same casual style, for example "it barely made the cut", "its overall score was one of the highest", "there weren't many headlines to go on", "one share is pretty pricey so it only got a little", "it got less money than the others because its score was lower". Don't force all three into every answer, and only mention what matters for that question. You may use the words "RSI", "headlines" and "price trend". Avoid other jargon ("composite", "SMA", "sentiment score", "proportional step", "leftover-cash step") unless the user used it first.

Use the tool data to pick the right verdict (these are guides for the three main factors; for any other reason, just describe it plainly from the data):
- RSI (the rsi field; think of it as how overheated the stock is). Under 30: RSI is low, it's been beaten down and might bounce. 30 to 55: RSI looks healthy. 55 to 70: RSI is getting a bit high. Over 70: RSI is high, the stock may be overheated. (Only under 30 or over 70 actually changes the score, but you can still mention a bit high in words.)
- Price trend (compare price to sma20): clearly above it means the price trend looks good (say really good if well above, about 8 percent or more). Slightly above means the price trend is okay. Below it means the price trend isn't great.
- Headlines (news_sentiment, from -1 to 1): above 0.5 means the headlines look really good. 0.15 to 0.5 means the headlines look pretty good. -0.15 to 0.15 means the headlines are mixed or meh. -0.5 to -0.15 means the headlines aren't looking too good. Below -0.5 means the headlines look bad.

Format for a why answer: one casual sentence with the verdict, then the two or three main reasons as casual verdicts, then at most one short line tying it together. Example: "Honestly it's a so-so pick. The RSI is a bit high and the price trend isn't great, but the headlines look pretty good, which kept it on the list." Don't list raw numbers (no RSI values, no scores, no percentages of the average) unless the user asks for the numbers or a number is the simplest way to make the point (like a share price). Keep most replies under about 60 words.

For share-count questions ("why only 1 share of X?"), use one real example from the data in plain words (for example: "I aimed to put about $240 in Microsoft, but one share costs $539, so I only got one using leftover cash"). Don't walk through the whole algorithm unless asked. If the user wants more depth, they'll ask.

Portfolio tracking (the app saves what the user owns in a file, so it is remembered tomorrow):
- Right after you give a fresh recommendation, end with one short line telling them to hit the "I bought these" button in the results window (or just tell you) so you remember it for tomorrow. Only say this right after a new recommendation.
- If they say they bought the stocks, call record_purchases with use_latest_recommendation true. If they bought different amounts or prices, use the purchases list. If they say they sold something, call record_sale.
- For "what do I own" or "how am I doing", call get_portfolio.
- For "should I buy, sell, keep or hold" (including "what do you want to do with your stocks"), call review_portfolio. A review window opens with the full table, so don't repeat it. Then give a short casual rundown: sells first, then buy more, then keeps, with a quick casual reason for each in the same verdict style as above. The rule: a score below 0 means SELL, 0 or higher means KEEP, and a score above 0.3 means it still looks strong. A strong stock only gets BUY MORE if the portfolio has cash for another share. The data says whether it does.
- The verdict comes from the score, not from profit or loss. You can mention how much a stock is up or down in a few words, but don't call something a keep just because it's up or a sell just because it's down.
- Every review is also saved as lines in review_log.csv (it opens in Excel), and the review window has a Copy table button. A review may have been run automatically earlier by the scheduled task. If so, its windows pop up when the app opens.
- If the user wants to see a chart or price history for a stock, call show_chart. It opens in its own window (with a button for the browser), so don't describe it in detail.
- Swaps: review_portfolio can return swap_ideas, where the weakest holding is sold to pay for a clearly stronger stock (it appears only when the score gap is big enough and the money covers at least one share). Scores run from -1 to 1, so never describe them as out of 100. Mention a swap casually with the real numbers from the data (what to sell, how many shares, what it buys). If the user wants new stocks considered as swap targets, run review_portfolio with include_new_ideas true. Only suggest swaps that are in swap_ideas, never make up your own. When swap ideas exist, a second window opens showing the portfolio after the swaps, with a button the user clicks once they've made them. If the user tells you in chat that they made all of the suggested swaps, call apply_swaps. If they made only some or did something different, use record_sale and record_purchases instead.
- You cannot trade. You only give opinions and track what the user tells you. The portfolio tracks cash (leftover money plus sale proceeds). NEVER suggest buying or adding with money that isn't there, and never offer to "size up" additions when cash is basically zero. If there's no cash, say they're fully invested and call the strong ones "strong keeps".
- If something is a SELL, you can say that selling it would free up about $X (cash_freed_if_the_sells_were_sold), which could go toward the strongest stocks they own. Just mention it as an option; don't act on it.
- When the user says they sold something, record it with record_sale so the cash updates. If they mention extra cash they have, use set_cash.
- If nothing is saved yet, say so and offer to put together a recommendation first.

Rules:
- When the user wants to invest or spend money, or asks what to buy, call get_recommendation with the dollar amount (understand "10k", "ten grand", "$5,000", etc.).
- NEVER invent tickers, prices, share counts, scores or headlines. Only use what the tools return.
- When explaining WHY something happened, base it only on the tool data and the split steps above. If the data doesn't explain it, say you're not sure instead of making up a reason. Never invent rules the code doesn't have (for example, there is no cap on how many shares or how much money one stock can get).
- After get_recommendation, the app has already opened a separate results window with the full allocation table (with a button for the headlines), so do NOT repeat the table. Mention the results are in that window, then give a short casual take: the biggest picks, leftover cash, anything notable. Finish with the one-line reminder about the "I bought these" button.
- If asked why, explain from the data you already have, or call get_stock_details for a specific stock.
- If the user wants to see headlines, news, articles or sources, call open_headlines so they open in the browser (set all_stocks to true if they want every stock's headlines, not just the picks). You can also mention a few headlines in chat.
- Plain text only. No markdown: no asterisks, no # headers, no tables. Short paragraphs or simple lines starting with a dash are fine.
- NO disclaimers. Never say you're not a financial advisor, never tell the user to do their own research, never remind them that this is just a suggestion or that you haven't bought anything, and don't add warnings. The user already knows. Just answer the question directly.
- Only talk about the strategy's limits if the user directly asks how reliable or risky it is, or if a limit is the direct answer to their question. In that case be honest and brief: it's a simple short-term strategy, it hasn't been backtested, and {NEWS_NOTE}. You only cover the S&P 500 list in the scanner.
- Casual small talk is fine, but steer back to the stocks."""


def load_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return key
    f = HERE / "api_key.txt"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    return ""


class Assistant:
    def __init__(self, emit, engine=None, client=None):
        self.emit = emit
        self.engine = engine or Engine()
        self.client = client
        self.history = []
        self.last = None
        self.portfolio = portfolio.load()
        self.review_scores = {}
        self.pending_swaps = None
        self.context = "recommendation"

    def say_status(self, text):
        self.emit("status", text)

    def ensure_data(self):
        if self.engine.scores:
            return
        if self.engine.load_cache():
            when = datetime.fromtimestamp(self.engine.scan_time).strftime("%b %d, %I:%M %p")
            self.say_status(f"Using saved scan from {when}.")
            return
        self.say_status("No fresh data yet, scanning the market. First time can take several minutes...")
        self.engine.scan(self.say_status)

    def tool_get_recommendation(self, amount):
        amount = float(amount)
        self.ensure_data()
        picks, cash = self.engine.allocate(amount)
        self.last = {"amount": amount, "picks": picks, "cash": cash, "recorded": False}
        self.context = "recommendation"
        scores = self.engine.scores

        if not picks:
            return json.dumps({"amount": amount, "picks": [],
                               "note": f"Nothing scored above the {THRESHOLD} buy threshold right now, so hold cash."})

        out, rows = [], []
        for p in picks:
            d = scores[p["ticker"]]
            pct = p["spent"] / amount * 100
            rows.append({"ticker": p["ticker"], "shares": p["shares"], "price": p["price"],
                         "cost": p["spent"], "score": d["composite"], "pct": pct})
            out.append({
                "ticker": p["ticker"], "shares": p["shares"], "price": round(p["price"], 2),
                "cost": round(p["spent"], 2), "actual_pct_of_money": round(pct, 1),
                "target_dollars": round(p["target"], 2), "target_pct_of_money": round(p["weight"] * 100, 1),
                "shares_from_proportional_step": p["first_pass"],
                "shares_from_leftover_cash_step": p["shares"] - p["first_pass"],
                "composite": d["composite"], "technical": d["tech"], "news_sentiment": d["sentiment"],
                "sma20": round(d["sma20"], 2), "rsi": round(d["rsi"], 1),
                "sma_signal": d["sma_signal"], "rsi_signal": d["rsi_signal"],
                "headline_count": len(d["headlines"]),
                "sample_headlines": [{"title": h["title"], "label": h["label"].lower()} for h in d["headlines"][:5]],
            })
        self.emit("results", {
            "amount": amount, "invested": amount - cash, "cash": cash, "rows": rows,
            "scan_time": datetime.fromtimestamp(self.engine.scan_time).strftime("%b %d, %I:%M %p"),
        })

        return json.dumps({
            "amount": amount, "cash_left": round(cash, 2), "buy_threshold": THRESHOLD,
            "stocks_with_news_scanned": len(scores),
            "scan_time": datetime.fromtimestamp(self.engine.scan_time).strftime("%b %d, %I:%M %p"),
            "picks": out,
            "note": "The allocation table is already shown in a separate results window. Don't repeat it.",
        })

    def all_scores(self):
        if not self.engine.scores and not self.review_scores:
            self.engine.load_cache()
        return {**self.engine.scores, **self.review_scores}

    def tool_open_headlines(self, tickers=None, all_stocks=False):
        scores = self.all_scores()
        if not scores:
            return json.dumps({"error": "No scan data yet. Run get_recommendation first."})
        title = "Headlines used for the picks"
        if all_stocks:
            wanted = sorted(scores, key=lambda t: scores[t]["composite"], reverse=True)
            title = f"Headlines for all {len(wanted)} scanned stocks"
        elif tickers:
            norm = [t.upper().replace(".", "-") for t in tickers]
            wanted = [t for t in norm if t in scores]
        elif self.context == "review" and self.review_scores:
            wanted = list(self.review_scores)
        elif self.last and self.last["picks"]:
            wanted = [p["ticker"] for p in self.last["picks"]]
        else:
            ranked = sorted(scores, key=lambda t: scores[t]["composite"], reverse=True)
            wanted = ranked[:10]
        if not wanted:
            return json.dumps({"error": "None of those tickers were news-scanned, so there are no headlines for them."})
        total = build_headlines_page(scores, wanted, REPORT_FILE, title)
        browser = open_in_browser(REPORT_FILE)
        return json.dumps({"opened_in": browser, "stocks_shown": len(wanted),
                           "tickers": wanted if len(wanted) <= 40 else wanted[:40] + ["...and more"],
                           "headline_count": total})

    def tool_get_stock_details(self, ticker):
        scores = self.all_scores()
        if not scores:
            return json.dumps({"error": "No scan data yet. Run get_recommendation first."})
        t = ticker.upper().replace(".", "-")
        d = scores.get(t)
        if not d:
            return json.dumps({"error": f"{t} wasn't news-scanned (only the strongest price-signal stocks and "
                                        f"stocks the user owns are), or it isn't in the S&P list."})
        alloc = None
        if self.last:
            for p in self.last["picks"]:
                if p["ticker"] == t:
                    alloc = {"shares": p["shares"], "cost": round(p["spent"], 2),
                             "target_dollars": round(p["target"], 2),
                             "target_pct_of_money": round(p["weight"] * 100, 1),
                             "shares_from_proportional_step": p["first_pass"],
                             "shares_from_leftover_cash_step": p["shares"] - p["first_pass"]}
        return json.dumps({
            "allocation_in_latest_recommendation": alloc,
            "ticker": t, "price": round(d["price"], 2), "sma20": round(d["sma20"], 2), "rsi": round(d["rsi"], 1),
            "sma_signal": d["sma_signal"], "rsi_signal": d["rsi_signal"], "technical": d["tech"],
            "news_sentiment": d["sentiment"], "composite": d["composite"],
            "would_buy": d["composite"] > THRESHOLD,
            "headlines": [{"title": h["title"], "label": h["label"].lower(), "score": h["score"],
                           "publisher": h["publisher"]} for h in d["headlines"][:10]],
        })

    def tool_record_purchases(self, use_latest=False, purchases=None):
        work = copy.deepcopy(self.portfolio)
        added = []
        known = self.all_scores()
        if use_latest:
            if not self.last or not self.last["picks"]:
                return json.dumps({"error": "There's no recommendation to save yet."})
            if self.last.get("recorded"):
                return json.dumps({"error": "That recommendation is already saved in the portfolio."})
            for p in self.last["picks"]:
                if p["shares"] > 0:
                    portfolio.add_purchase(work, p["ticker"], p["shares"], p["price"], from_cash=False)
                    added.append({"ticker": p["ticker"], "shares": p["shares"], "price": round(p["price"], 2)})
        if use_latest:
            portfolio.apply_recommendation_cash(work, self.last["amount"],
                                                sum(p["spent"] for p in self.last["picks"]))
        for item in purchases or []:
            t = item["ticker"].upper().replace(".", "-")
            price = item.get("price") or (known.get(t) or {}).get("price")
            if not price:
                return json.dumps({"error": f"I don't have a price for {t}. Ask the user what they paid per share."})
            portfolio.add_purchase(work, t, item["shares"], price)
            added.append({"ticker": t, "shares": int(item["shares"]), "price": round(float(price), 2)})
        if not added:
            return json.dumps({"error": "Nothing to save."})
        portfolio.save(work)
        self.portfolio = work
        if use_latest:
            self.last["recorded"] = True
        return json.dumps({"saved": added, "stocks_now_in_portfolio": len(work["holdings"]),
                           "cash_now": round(work.get("cash", 0.0), 2),
                           "note": "Recommendation purchases are saved at the scanned (last close) prices. "
                                   "If the user paid different prices they can tell you."})

    def tool_record_sale(self, ticker, shares=None, price=None):
        t = ticker.upper().replace(".", "-")
        if not price:
            price = (self.all_scores().get(t) or {}).get("price")
        if not price:
            return json.dumps({"error": f"I don't have a recent price for {t}. Ask the user what price they sold at."})
        work = copy.deepcopy(self.portfolio)
        sold = portfolio.record_sale(work, t, shares, price)
        portfolio.save(work)
        self.portfolio = work
        return json.dumps({"sold": t, "shares_sold": sold, "sold_at": round(float(price), 2),
                           "shares_left": work["holdings"].get(t, {}).get("shares", 0),
                           "cash_now": round(work.get("cash", 0.0), 2)})

    def tool_apply_swaps(self):
        ps = self.pending_swaps
        if not ps or not ps["swaps"]:
            return json.dumps({"error": "There are no swap suggestions to save. Run a portfolio review first."})
        if ps["applied"]:
            return json.dumps({"error": "Those swaps are already saved."})
        holdings = self.portfolio["holdings"]
        for sw in ps["swaps"]:
            w = sw["sell"]
            if holdings.get(w["ticker"], {}).get("shares", 0) < w["shares"]:
                return json.dumps({"error": f"Your portfolio changed since the review ({w['ticker']} isn't there "
                                            f"anymore). Run the review again."})
        work = copy.deepcopy(self.portfolio)
        apply_swaps_to_portfolio(work, ps["swaps"])
        portfolio.save(work)
        self.portfolio = work
        ps["applied"] = True
        return json.dumps({"swapped": [{"sold": sw["sell"]["ticker"], "bought": sw["buy"]["ticker"],
                                        "shares_bought": sw["buy"]["shares"]} for sw in ps["swaps"]],
                           "cash_now": round(work.get("cash", 0.0), 2),
                           "stocks_in_portfolio": len(work["holdings"])})

    def tool_set_cash(self, amount):
        amount = float(amount)
        if amount < 0:
            return json.dumps({"error": "Cash can't be negative."})
        work = copy.deepcopy(self.portfolio)
        work["cash"] = round(amount, 2)
        portfolio.save(work)
        self.portfolio = work
        return json.dumps({"cash_now": work["cash"]})

    def tool_get_portfolio(self):
        holdings = self.portfolio["holdings"]
        if not holdings:
            return json.dumps({"holdings": [], "note": "Nothing saved yet."})
        prices = self.engine.quote(list(holdings))
        rows, value, cost = [], 0.0, 0.0
        for t, h in holdings.items():
            now = prices.get(t)
            row = {"ticker": t, "shares": h["shares"], "bought_at": h["avg_cost"], "first_bought": h["first_bought"]}
            cost += h["avg_cost"] * h["shares"]
            if now:
                row.update({"price_now": round(now, 2), "profit_loss_pct": round((now / h["avg_cost"] - 1) * 100, 1)})
                value += now * h["shares"]
            else:
                row["price_now"] = None
            rows.append(row)
        return json.dumps({"holdings": rows, "cash": round(self.portfolio.get("cash", 0.0), 2),
                           "total_paid": round(cost, 2), "total_value_now": round(value, 2),
                           "note": "Prices are the latest close. Total value only counts stocks with a price."})

    def market_scores(self, fresh_scan=False):
        e = self.engine

        def fresh():
            return bool(e.scores) and e.scan_time and (time.time() - e.scan_time) / 3600 <= eng.CACHE_HOURS

        if not fresh():
            e.load_cache()
        if not fresh() and fresh_scan:
            self.say_status("Scanning the whole market for new ideas. This takes a few minutes...")
            e.scan(self.say_status)
        return e.scores if fresh() else {}

    def _show_review(self, result):
        self.review_scores = result["review_scores"]
        self.context = "review"
        if result["portfolio_payload"]:
            self.emit("portfolio", result["portfolio_payload"])
        self.pending_swaps = {"swaps": result["swaps"], "applied": False}
        if result["after_payload"]:
            self.emit("portfolio_after", result["after_payload"])

    def tool_review_portfolio(self, include_new_ideas=False):
        result = review.run_review(self.engine, self.portfolio, self.say_status, include_new_ideas=include_new_ideas)
        if result.get("error"):
            return json.dumps({"error": result["error"]})
        self._show_review(result)
        try:
            portfolio.append_review_log(result["rows"])
        except Exception as e:
            self.say_status(f"(Couldn't add this review to review_log.csv: {e})")
        return json.dumps(review.claude_view(result))

    def show_saved_review(self):
        data = review.load_latest()
        if not data or data.get("shown") or "result" not in data:
            return
        review.mark_shown()
        when = data.get("when", "earlier")
        try:
            if os.path.exists(portfolio.FILE) and os.path.getmtime(portfolio.FILE) > data["timestamp"]:
                self.say_status(f"A scheduled review from {when} was waiting, but your portfolio changed after it ran, "
                                f"so I skipped it. Ask me to review again for a fresh one.")
                return
            self.say_status(f"Your scheduled review from {when} is ready. The review windows are open.")
            self._show_review(data["result"])
        except Exception as e:
            self.say_status(f"Couldn't open the saved review: {e}")

    def tool_show_chart(self, ticker, period="6mo"):
        import yfinance as yf
        ticker = re.sub(r"[^A-Za-z0-9\-]", "", ticker.upper().replace(".", "-"))
        if not ticker:
            return json.dumps({"error": "That doesn't look like a ticker symbol."})
        if period not in charts.PERIODS:
            period = "6mo"
        self.say_status(f"Getting the {charts.PERIOD_NAME[period]} price history for {ticker}...")
        df = yf.download(ticker, period=charts.FETCH_PERIOD[period], group_by="ticker",
                         progress=False, auto_adjust=True)
        closes = Engine._closes(df, ticker)
        if len(closes) < 30:
            return json.dumps({"error": f"Couldn't get enough price history for {ticker}."})
        data = charts.chart_data(closes, period)
        h = self.portfolio["holdings"].get(ticker)
        path = HERE / f"chart_{ticker}.html"
        buy = h["avg_cost"] if h else None
        stats = charts.write_chart_page(path, ticker, data, buy)
        self.emit("chart", {"ticker": ticker, "data": data, "buy_price": buy, "html_path": str(path)})
        return json.dumps({"opened_in": "a chart window in the app", "ticker": ticker, "period": period, **stats})

    def open_page(self, path):
        return open_in_browser(path)

    def tool_refresh_scan(self):
        if os.path.exists(eng.CACHE_FILE):
            os.remove(eng.CACHE_FILE)
        self.engine.scores = {}
        self.say_status("Re-scanning the market with fresh data...")
        self.engine.scan(self.say_status)
        return json.dumps({"status": "refreshed", "stocks_with_news_scanned": len(self.engine.scores)})

    def run_tool(self, name, args):
        try:
            if name == "get_recommendation":
                return self.tool_get_recommendation(args["amount"]), False
            if name == "open_headlines":
                return self.tool_open_headlines(args.get("tickers"), args.get("all_stocks", False)), False
            if name == "get_stock_details":
                return self.tool_get_stock_details(args["ticker"]), False
            if name == "show_chart":
                return self.tool_show_chart(args["ticker"], args.get("period", "6mo")), False
            if name == "refresh_scan":
                return self.tool_refresh_scan(), False
            if name == "record_purchases":
                return self.tool_record_purchases(args.get("use_latest_recommendation", False),
                                                  args.get("purchases")), False
            if name == "record_sale":
                return self.tool_record_sale(args["ticker"], args.get("shares"), args.get("price")), False
            if name == "apply_swaps":
                return self.tool_apply_swaps(), False
            if name == "set_cash":
                return self.tool_set_cash(args["amount"]), False
            if name == "get_portfolio":
                return self.tool_get_portfolio(), False
            if name == "review_portfolio":
                return self.tool_review_portfolio(args.get("include_new_ideas", False)), False
            return json.dumps({"error": f"unknown tool {name}"}), True
        except Exception as e:
            return json.dumps({"error": str(e)}), True

    def handle(self, user_text):
        start_len = len(self.history)
        self.history.append({"role": "user", "content": user_text})
        try:
            if self.client is None:
                self.client = anthropic.Anthropic(api_key=load_api_key())
            for _ in range(8):
                resp = self.client.messages.create(
                    model=MODEL, max_tokens=1500, system=SYSTEM_PROMPT,
                    tools=TOOLS, messages=self.history)
                self.history.append({"role": "assistant", "content": resp.content})
                if resp.stop_reason == "tool_use":
                    results = []
                    for block in resp.content:
                        if block.type == "tool_use":
                            content, is_error = self.run_tool(block.name, block.input)
                            r = {"type": "tool_result", "tool_use_id": block.id, "content": content}
                            if is_error:
                                r["is_error"] = True
                            results.append(r)
                    self.history.append({"role": "user", "content": results})
                    continue
                text = "".join(b.text for b in resp.content if b.type == "text").strip()
                self.emit("bot", text or "(no reply)")
                return
            self.emit("bot", "Sorry, I got stuck going back and forth with my tools. Try asking again?")
        except anthropic.AuthenticationError:
            del self.history[start_len:]
            self.emit("bot", "Your API key was rejected. Check api_key.txt (no extra spaces or quotes) "
                             "and make sure the key is still active in the Anthropic console.")
        except Exception as e:
            del self.history[start_len:]
            msg = str(e)
            if "credit balance" in msg.lower():
                msg = "Your Anthropic account is out of credits. Add some in the console under Billing."
            self.emit("bot", f"Something went wrong: {msg}")


class ResultsWindow(tk.Toplevel):
    SAVE_TEXT = "I bought these (save)"
    SAVED_TEXT = "Saved to portfolio"
    COLS = [("ticker", "Ticker", 90, "w"), ("shares", "Shares", 80, "e"), ("price", "Price", 100, "e"),
            ("cost", "Cost", 120, "e"), ("score", "Score", 80, "e"), ("pct", "% of money", 110, "e")]

    def __init__(self, master, on_headlines, on_save=None):
        super().__init__(master)
        self.title("MRidge - your split")
        self.geometry("680x500")
        self.configure(bg="#1e1e1e")
        self.rows = []
        self.sort_col = None
        self.sort_rev = False

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", background="#252526", fieldbackground="#252526", foreground="#e6e6e6",
                        rowheight=30, font=("Consolas", 11), borderwidth=0)
        style.configure("Treeview.Heading", background="#333333", foreground="white",
                        font=("Consolas", 11, "bold"), relief="flat")
        style.map("Treeview", background=[("selected", "#0e639c")])
        style.map("Treeview.Heading", background=[("active", "#444444")])

        self.header = tk.Label(self, text="", bg="#1e1e1e", fg="#e6e6e6", font=("Consolas", 15, "bold"), anchor="w")
        self.header.pack(fill=tk.X, padx=14, pady=(14, 0))
        self.summary = tk.Label(self, text="", bg="#1e1e1e", fg="#9be39b", font=("Consolas", 11), anchor="w", justify="left")
        self.summary.pack(fill=tk.X, padx=14, pady=(2, 8))

        frame = tk.Frame(self, bg="#1e1e1e")
        frame.pack(fill=tk.BOTH, expand=True, padx=14)
        self.tree = ttk.Treeview(frame, columns=[c[0] for c in self.COLS], show="headings", selectmode="browse")
        for key, label, width, anchor in self.COLS:
            self.tree.heading(key, text=label, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, anchor=anchor)
        bar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        bar.pack(side=tk.RIGHT, fill=tk.Y)

        self.btns = btns = tk.Frame(self, bg="#1e1e1e")
        btns.pack(fill=tk.X, padx=14, pady=12)
        tk.Button(btns, text="Open headlines in browser", command=on_headlines, font=("Consolas", 11, "bold"),
                  bg="#0e639c", fg="white", relief="flat", padx=14, pady=6).pack(side=tk.LEFT)
        tk.Button(btns, text="Copy table", command=self.copy_table, font=("Consolas", 11),
                  bg="#3a3a3a", fg="white", relief="flat", padx=14, pady=6).pack(side=tk.LEFT, padx=8)
        self.save_btn = None
        if on_save:
            self.save_btn = tk.Button(btns, text=self.SAVE_TEXT, command=on_save, font=("Consolas", 11, "bold"),
                                      bg="#2e7d32", fg="white", relief="flat", padx=14, pady=6)
            self.save_btn.pack(side=tk.LEFT)
        self.hint = tk.Label(btns, text="", bg="#1e1e1e", fg="#777777", font=("Consolas", 9))
        self.hint.pack(side=tk.RIGHT)

    def update_data(self, data):
        self.rows = list(data["rows"])
        self.sort_col = None
        self.header.config(text=f"Your ${data['amount']:,.0f} split")
        self.summary.config(text=f"Invested: ${data['invested']:,.2f}    Cash left: ${data['cash']:,.2f}\n"
                                 f"Based on scan from {data['scan_time']}")
        if self.save_btn:
            self.save_btn.config(text=self.SAVE_TEXT, state="normal", bg="#2e7d32")
        self.refresh()
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))

    def flash_copied(self):
        self.hint.config(text="copied!")
        self.after(2000, lambda: self.hint.config(text="") if self.winfo_exists() else None)

    def mark_saved(self):
        if self.save_btn:
            self.save_btn.config(text=self.SAVED_TEXT, state="disabled", bg="#555555")

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.rows:
            self.tree.insert("", tk.END, values=(
                r["ticker"], r["shares"], f"${r['price']:,.2f}", f"${r['cost']:,.2f}",
                f"{r['score']:+.2f}", f"{r['pct']:.1f}%"))

    def sort_by(self, col):
        self.sort_rev = (not self.sort_rev) if self.sort_col == col else (col != "ticker")
        self.sort_col = col
        self.rows.sort(key=lambda r: r[col], reverse=self.sort_rev)
        self.refresh()

    def copy_table(self):
        lines = ["Ticker\tShares\tPrice\tCost\tScore\t% of money"]
        for r in self.rows:
            lines.append(f"{r['ticker']}\t{r['shares']}\t{r['price']:.2f}\t{r['cost']:.2f}\t{r['score']:+.2f}\t{r['pct']:.1f}")
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.flash_copied()


class PortfolioWindow(ResultsWindow):
    COLS = [("ticker", "Ticker", 80, "w"), ("shares", "Shares", 70, "e"), ("avg_cost", "Bought at", 100, "e"),
            ("price", "Now", 100, "e"), ("pl_pct", "Profit/Loss", 105, "e"), ("score", "Score", 70, "e"),
            ("verdict", "Verdict", 100, "center")]
    COLORS = {"SELL": "#ff7b7b", "SWAP OUT": "#ffb454", "KEEP": "#e6e6e6", "BUY MORE": "#7fe08a"}

    def __init__(self, master, on_headlines, on_save=None):
        super().__init__(master, on_headlines, on_save=on_save)
        self.title("MRidge - portfolio review")
        self.geometry("780x520+40+40")
        for name, color in self.COLORS.items():
            self.tree.tag_configure(name, foreground=color)

    def update_data(self, data):
        self.rows = list(data["rows"])
        self.sort_col = None
        self.header.config(text="Your portfolio review")
        sign = "+" if data["total_pl"] >= 0 else "-"
        text = (f"Worth now if you swap nothing: ${data['total_value']:,.2f}    Paid: ${data['total_cost']:,.2f}    "
                f"Total: {sign}${abs(data['total_pl']):,.2f} ({round(data['total_pl_pct'], 1) + 0.0:+.1f}%)\n"
                f"Cash: ${data.get('cash', 0.0):,.2f}    Fresh check: {data['when']}")
        for sw in data.get("swaps", []):
            w, b = sw["sell"], sw["buy"]
            text += (f"\nSwap idea: sell {w['ticker']} ({w['shares']} sh, ${w['proceeds']:,.0f}) "
                     f"-> buy {b['ticker']} ({b['shares']} sh)")
        if data["missing"]:
            text += "\nNo data for: " + ", ".join(data["missing"])
        self.summary.config(text=text)
        if self.save_btn:
            self.save_btn.config(text=self.SAVE_TEXT, state="normal", bg="#2e7d32")
        self.refresh()
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for r in self.rows:
            self.tree.insert("", tk.END, tags=(r["verdict"],), values=(
                r["ticker"], r["shares"], f"${r['avg_cost']:,.2f}", f"${r['price']:,.2f}",
                f"{round(r['pl_pct'], 1) + 0.0:+.1f}%", f"{r['score']:+.2f}", r["verdict"]))

    def copy_table(self):
        lines = ["Ticker\tShares\tBought at\tNow\tProfit/Loss %\tScore\tVerdict"]
        for r in self.rows:
            lines.append(f"{r['ticker']}\t{r['shares']}\t{r['avg_cost']:.2f}\t{r['price']:.2f}\t"
                         f"{round(r['pl_pct'], 1) + 0.0:+.1f}\t{r['score']:+.2f}\t{r['verdict']}")
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))
        self.flash_copied()


class AfterSwapWindow(PortfolioWindow):
    COLORS = {"NEW": "#7fe08a", "ADDED": "#7fe08a", "KEEP": "#e6e6e6"}
    SAVE_TEXT = "I made these swaps (save)"
    SAVED_TEXT = "Swaps saved"

    def __init__(self, master, on_headlines, on_save=None):
        super().__init__(master, on_headlines, on_save=on_save)
        self.title("MRidge - portfolio after swaps")
        self.geometry("780x560+170+110")

    def update_data(self, data):
        self.rows = list(data["rows"])
        self.sort_col = None
        self.header.config(text="Your portfolio after the swaps")
        text = (f"Worth after swaps: ${data['total_after']:,.2f}  (stocks ${data['stocks_value']:,.2f} "
                f"+ cash ${data['cash']:,.2f})    Before: ${data['total_before']:,.2f}")
        for sw in data["swaps"]:
            w, b = sw["sell"], sw["buy"]
            text += f"\nSell {w['ticker']} ({w['shares']} sh)  ->  buy {b['ticker']} ({b['shares']} sh)"
        text += "\nClick the green button once you've made these trades, so I remember them."
        self.summary.config(text=text)
        if self.save_btn:
            self.save_btn.config(text=self.SAVE_TEXT, state="normal", bg="#2e7d32")
        self.refresh()
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))


class ChatApp:
    def __init__(self, root):
        self.root = root
        root.title("MRidge - Stock Chat (S&P 500)")
        root.geometry("860x660")
        root.configure(bg="#1e1e1e")

        self.msgs = queue.Queue()
        self.busy = False
        self.results_win = None
        self.portfolio_win = None
        self.swapped_win = None
        reset_note = portfolio.auto_reset(EXPERIMENT_START)
        self.assistant = Assistant(emit=lambda kind, text: self.msgs.put((kind, text)))

        self.chat = scrolledtext.ScrolledText(
            root, wrap=tk.WORD, state="disabled", bg="#252526", fg="#e6e6e6",
            font=("Consolas", 11), padx=10, pady=10, relief="flat")
        self.chat.pack(fill=tk.BOTH, expand=True, padx=10, pady=(10, 5))
        self.chat.tag_config("you", foreground="#7fc8ff", font=("Consolas", 11, "bold"))
        self.chat.tag_config("botname", foreground="#9be39b", font=("Consolas", 11, "bold"))
        self.chat.tag_config("bot", foreground="#e6e6e6")
        self.chat.tag_config("table", foreground="#f0e68c", font=("Consolas", 11))
        self.chat.tag_config("status", foreground="#9a9a9a", font=("Consolas", 10, "italic"))

        bottom = tk.Frame(root, bg="#1e1e1e")
        bottom.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.entry = tk.Entry(bottom, font=("Consolas", 12), bg="#333333", fg="white",
                              insertbackground="white", relief="flat")
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8, padx=(0, 8))
        self.entry.bind("<Return>", lambda e: self.on_send())
        self.send_btn = tk.Button(bottom, text="Send", command=self.on_send, font=("Consolas", 11, "bold"),
                                  bg="#0e639c", fg="white", relief="flat", padx=18)
        self.send_btn.pack(side=tk.RIGHT)
        self.entry.focus()

        if reset_note:
            self.write("status", reset_note)
        if load_api_key():
            self.write("bot", "yo! I'm MRidge. tell me how much you wanna invest and I'll figure out how to split it up "
                              "across S&P 500 stocks. ask me anything after, like why I picked something, "
                              "or to pull up the headlines. once you buy, I can track your stocks and tell you "
                              "tomorrow whether to keep, sell or buy more.")
        else:
            self.write("status", "No API key found. Create a file called api_key.txt in this folder, paste your "
                                 "Anthropic API key in it, save, and restart the app.")
        self.root.after(100, self.poll)
        self.assistant.show_saved_review()

    def write(self, kind, text):
        self.chat.config(state="normal")
        if kind == "you":
            self.chat.insert(tk.END, "You: ", "you")
            self.chat.insert(tk.END, text + "\n\n", "bot")
        elif kind == "bot":
            self.chat.insert(tk.END, "MRidge: ", "botname")
            self.chat.insert(tk.END, text + "\n\n", "bot")
        elif kind == "table":
            self.chat.insert(tk.END, text + "\n\n", "table")
        else:
            self.chat.insert(tk.END, text + "\n", "status")
        self.chat.config(state="disabled")
        self.chat.see(tk.END)

    def set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.entry.config(state=state)
        self.send_btn.config(state=state)
        if not busy:
            self.entry.focus()

    def poll(self):
        try:
            while True:
                kind, text = self.msgs.get_nowait()
                if kind == "done":
                    self.set_busy(False)
                elif kind == "results":
                    self.show_results(text)
                elif kind == "portfolio":
                    self.show_portfolio(text)
                elif kind == "portfolio_after":
                    self.show_swapped(text)
                elif kind == "chart":
                    self.show_chart(text)
                else:
                    self.write(kind, text)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def show_results(self, data):
        if self.results_win is None or not self.results_win.winfo_exists():
            self.results_win = ResultsWindow(
                self.root,
                on_headlines=lambda: self.open_headlines_clicked(
                    [p["ticker"] for p in (self.assistant.last or {}).get("picks", [])]),
                on_save=self.save_clicked)
        self.results_win.update_data(data)

    def show_portfolio(self, data):
        if self.portfolio_win is None or not self.portfolio_win.winfo_exists():
            self.portfolio_win = PortfolioWindow(
                self.root, on_headlines=lambda: self.open_headlines_clicked(list(self.assistant.review_scores)))
        self.portfolio_win.update_data(data)
        if not data.get("swaps") and self.swapped_win is not None and self.swapped_win.winfo_exists():
            self.swapped_win.destroy()
            self.swapped_win = None

    def show_chart(self, p):
        import chart_window
        chart_window.ChartWindow(self.root, p["ticker"], p["data"], p["buy_price"],
                                 on_browser=lambda: self.assistant.open_page(p["html_path"]))

    def show_swapped(self, data):
        if self.swapped_win is None or not self.swapped_win.winfo_exists():
            self.swapped_win = AfterSwapWindow(
                self.root,
                on_headlines=lambda: self.open_headlines_clicked([r["ticker"] for r in self.swapped_win.rows]),
                on_save=self.apply_swaps_clicked)
        self.swapped_win.update_data(data)

    def apply_swaps_clicked(self):
        try:
            result = json.loads(self.assistant.tool_apply_swaps())
        except Exception as e:
            self.write("status", f"Couldn't save the swaps: {e}")
            return
        if "error" in result:
            self.write("status", "Couldn't save the swaps: " + result["error"])
            return
        n = len(result["swapped"])
        self.write("status", f"Saved {n} swap(s). Your portfolio and cash are updated (cash now ${result['cash_now']:,.2f}).")
        self.swapped_win.mark_saved()

    def open_headlines_clicked(self, tickers):
        try:
            result = self.assistant.tool_open_headlines(tickers or None)
            if '"error"' in result:
                self.write("status", "Couldn't open headlines: " + json.loads(result)["error"])
        except Exception as e:
            self.write("status", f"Couldn't open headlines: {e}")

    def save_clicked(self):
        try:
            result = json.loads(self.assistant.tool_record_purchases(use_latest=True))
        except Exception as e:
            self.write("status", f"Couldn't save: {e}")
            return
        if "error" in result:
            self.write("status", "Couldn't save: " + result["error"])
        else:
            n = len(result["saved"])
            self.write("status", f"Saved {n} stocks to your portfolio. Tomorrow, ask me whether to keep, sell "
                                 f"or buy more.")
            self.results_win.mark_saved()

    def on_send(self):
        text = self.entry.get().strip()
        if not text or self.busy:
            return
        if not load_api_key():
            self.write("status", "No API key yet. Put it in api_key.txt and restart the app.")
            return
        self.entry.delete(0, tk.END)
        self.write("you", text)
        self.set_busy(True)
        self.write("status", "thinking...")
        threading.Thread(target=self.worker, args=(text,), daemon=True).start()

    def worker(self, text):
        try:
            self.assistant.handle(text)
        finally:
            self.msgs.put(("done", ""))


def main():
    if sys.stdout is None or sys.stderr is None:
        log = open(HERE / "app.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or log
        sys.stderr = sys.stderr or log
    if USE_MODERN_UI:
        try:
            import customtkinter
            import modern_ui
            modern_ui.run(sys.modules[__name__])
            return
        except ImportError as e:
            print(f"Couldn't load the modern look ({e}). Run:  py -3.12 -m pip install customtkinter")
            print("Starting the classic look instead.")
        except Exception:
            traceback.print_exc()
            print("The modern look hit an error. Starting the classic look instead.")
    root = tk.Tk()
    ChatApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
