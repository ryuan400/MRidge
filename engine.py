import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import yfinance as yf
from gnews import GNews

from tickers import TICKERS

THRESHOLD = 0.3
SELL_BELOW = 0.0
SWAP_GAP = 0.3
MAX_SWAPS = 3
NEWS_CANDIDATES = None
CACHE_HOURS = 4
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scan_cache.json")
MODEL_NAME = "yiyanghkust/finbert-tone"
MAX_HEADLINES = 8
NEWS_WORKERS = 6

YF_TICKERS = [t.replace(".", "-") for t in TICKERS]


def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def get_headlines(ticker):
    try:
        news = yf.Ticker(ticker).news
    except Exception:
        news = None
    if news:
        return news, "yfinance"
    try:
        return GNews(max_results=MAX_HEADLINES, period="7d").get_news(ticker + " stock"), "gnews"
    except Exception:
        return [], "gnews"


def extract_headline(item, source):
    if source == "yfinance":
        content = item.get("content", {}) or {}
        title = item.get("title") or content.get("title")
        link = (content.get("canonicalUrl") or {}).get("url") or item.get("link") or ""
        publisher = (content.get("provider") or {}).get("displayName") or item.get("publisher") or ""
    else:
        title = item.get("title")
        link = item.get("url") or ""
        publisher = (item.get("publisher") or {}).get("title", "")
    return title, link, publisher


class Engine:
    def __init__(self):
        self.pipe = None
        self.scores = {}
        self.scan_time = None

    def load_model(self, say):
        if self.pipe is not None:
            return
        say("Loading the FinBERT news-sentiment model (first time downloads ~400MB)...")
        from transformers import BertForSequenceClassification, BertTokenizer, pipeline
        model = BertForSequenceClassification.from_pretrained(MODEL_NAME, num_labels=3)
        tokenizer = BertTokenizer.from_pretrained(MODEL_NAME)
        self.pipe = pipeline("sentiment-analysis", model=model, tokenizer=tokenizer, device=-1)

    def score_title(self, title):
        result = self.pipe(title, truncation=True, max_length=512)[0]
        label = result["label"].upper()
        conf = float(result["score"])
        if label == "POSITIVE":
            return label, conf
        if label == "NEGATIVE":
            return label, -conf
        return label, 0.0

    def load_cache(self):
        if not os.path.exists(CACHE_FILE):
            return False
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            age_hours = (time.time() - data["time"]) / 3600
            if age_hours > CACHE_HOURS:
                return False
            self.scores = data["scores"]
            self.scan_time = data["time"]
            return True
        except Exception:
            return False

    def save_cache(self):
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump({"time": self.scan_time, "scores": self.scores}, f)

    @staticmethod
    def _closes(df, t):
        try:
            c = df[t]["Close"]
        except Exception:
            c = df["Close"]
        if hasattr(c, "columns"):
            c = c.iloc[:, 0]
        return c.dropna()

    def compute_tech(self, tickers, say):
        say(f"Downloading 6 months of prices for {len(tickers)} stocks...")
        tech = {}
        batch_size = 100
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i:i + batch_size]
            try:
                df = yf.download(batch, period="6mo", group_by="ticker",
                                 threads=True, progress=False, auto_adjust=True)
            except Exception as e:
                say(f"  (a price batch failed: {e})")
                continue
            for t in batch:
                try:
                    closes = self._closes(df, t)
                    if len(closes) < 30:
                        continue
                    price = float(closes.iloc[-1])
                    sma20 = float(closes.rolling(20).mean().iloc[-1])
                    rsi = float(calculate_rsi(closes).iloc[-1])
                    if np.isnan(rsi) or np.isnan(sma20):
                        continue
                    sma_signal = 0.5 if price > sma20 else -0.5
                    rsi_signal = 0.5 if rsi < 30 else (-0.5 if rsi > 70 else 0.0)
                    momentum = price / float(closes.iloc[-20]) - 1
                    tech[t] = {
                        "price": price, "sma20": sma20, "rsi": rsi,
                        "sma_signal": sma_signal, "rsi_signal": rsi_signal,
                        "tech": round(sma_signal + rsi_signal, 2),
                        "momentum": momentum,
                    }
                except Exception:
                    continue
            if len(tickers) > batch_size:
                say(f"  prices done for {min(i + batch_size, len(tickers))}/{len(tickers)}")
        return tech

    def quote(self, tickers):
        prices = {}
        try:
            df = yf.download(list(tickers), period="5d", group_by="ticker",
                             threads=True, progress=False, auto_adjust=True)
        except Exception:
            return prices
        for t in tickers:
            try:
                c = self._closes(df, t)
                if len(c):
                    prices[t] = float(c.iloc[-1])
            except Exception:
                pass
        return prices

    def score_with_news(self, candidates, tech, say):
        raw = {}
        done = 0
        with ThreadPoolExecutor(max_workers=NEWS_WORKERS) as pool:
            futures = {pool.submit(get_headlines, t): t for t in candidates}
            for fut in as_completed(futures):
                t = futures[fut]
                try:
                    items, source = fut.result()
                except Exception:
                    items, source = [], "gnews"
                heads = []
                for item in items:
                    title, link, publisher = extract_headline(item, source)
                    if title:
                        heads.append({"title": title, "link": link, "publisher": publisher, "source": source})
                    if len(heads) >= MAX_HEADLINES:
                        break
                raw[t] = heads
                done += 1
                if done % 10 == 0 or done == len(candidates):
                    say(f"  fetched news for {done}/{len(candidates)}")

        all_titles = [h["title"] for t in candidates for h in raw[t]]
        say(f"Scoring {len(all_titles)} headlines with FinBERT...")
        results = []
        if all_titles:
            results = self.pipe(all_titles, batch_size=16, truncation=True, max_length=128)

        scores = {}
        i = 0
        for t in candidates:
            heads, total = [], 0.0
            for h in raw[t]:
                r = results[i]
                i += 1
                label = r["label"].upper()
                conf = float(r["score"])
                score = conf if label == "POSITIVE" else (-conf if label == "NEGATIVE" else 0.0)
                h["label"] = label
                h["score"] = round(score, 2)
                heads.append(h)
                total += score
            sentiment = round(total / len(heads), 2) if heads else 0.0
            d = dict(tech[t])
            d["sentiment"] = sentiment
            d["composite"] = round(0.6 * d["tech"] + 0.4 * sentiment, 2)
            d["headlines"] = heads
            scores[t] = d
        return scores

    def scan(self, say):
        self.load_model(say)
        tech = self.compute_tech(YF_TICKERS, say)
        if not tech:
            raise RuntimeError("Couldn't download any prices. Check your internet connection.")

        ranked = sorted(tech, key=lambda t: (tech[t]["tech"], tech[t]["momentum"]), reverse=True)
        candidates = ranked if NEWS_CANDIDATES is None else ranked[:NEWS_CANDIDATES]
        say(f"Reading news for the {len(candidates)} stocks with the strongest price signals...")
        scores = self.score_with_news(candidates, tech, say)

        self.scores = scores
        self.scan_time = time.time()
        self.save_cache()

    def score_tickers(self, tickers, say):
        self.load_model(say)
        tech = self.compute_tech(tickers, say)
        found = [t for t in tickers if t in tech]
        missing = [t for t in tickers if t not in tech]
        scores = self.score_with_news(found, tech, say) if found else {}
        return scores, missing

    def allocate(self, amount):
        eligible = {t: d for t, d in self.scores.items() if d["composite"] > THRESHOLD}
        if not eligible:
            return [], amount

        total_weight = sum(d["composite"] for d in eligible.values())
        picks = {t: {"shares": 0, "spent": 0.0, "price": d["price"], "weight": d["composite"] / total_weight,
                       "target": amount * d["composite"] / total_weight, "first_pass": 0}
                 for t, d in eligible.items()}

        cash = amount

        for t, d in sorted(eligible.items(), key=lambda kv: -kv[1]["composite"]):
            target = amount * (d["composite"] / total_weight)
            shares = int(target // d["price"])
            if shares > 0:
                spent = shares * d["price"]
                cash -= spent
                picks[t]["shares"] = shares
                picks[t]["first_pass"] = shares
                picks[t]["spent"] = spent

        bought_more = True
        while bought_more:
            bought_more = False
            for t, d in sorted(eligible.items(), key=lambda kv: (-kv[1]["composite"], kv[1]["price"])):
                if cash >= d["price"]:
                    cash -= d["price"]
                    picks[t]["shares"] += 1
                    picks[t]["spent"] += d["price"]
                    bought_more = True

        result_picks = []
        for t, p in sorted(picks.items(), key=lambda kv: -eligible[kv[0]]["composite"]):
            if p["shares"] > 0:
                result_picks.append({
                    "ticker": t, "shares": p["shares"], "price": p["price"],
                    "spent": p["spent"], "weight": p["weight"],
                    "target": p["target"], "first_pass": p["first_pass"]
                })

        return result_picks, cash
