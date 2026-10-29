import csv
import json
import os
from datetime import datetime

FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.json")


def load():
    if os.path.exists(FILE):
        try:
            with open(FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            data.setdefault("holdings", {})
            data.setdefault("log", [])
            data.setdefault("cash", 0.0)
            return data
        except Exception:
            pass
    return {"holdings": {}, "log": [], "cash": 0.0}


def save(p):
    tmp = FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2)
    os.replace(tmp, FILE)


def add_purchase(p, ticker, shares, price, from_cash=True):
    ticker = ticker.upper().replace(".", "-")
    shares = int(shares)
    price = float(price)
    if shares <= 0 or price <= 0:
        raise ValueError(f"{ticker}: shares and price must be positive")
    h = p["holdings"].get(ticker)
    if h:
        total_shares = h["shares"] + shares
        h["avg_cost"] = round((h["avg_cost"] * h["shares"] + price * shares) / total_shares, 4)
        h["shares"] = total_shares
    else:
        p["holdings"][ticker] = {"shares": shares, "avg_cost": round(price, 4),
                                 "first_bought": datetime.now().strftime("%Y-%m-%d")}
    if from_cash:
        p["cash"] = max(0.0, round(p.get("cash", 0.0) - shares * price, 2))
    p["log"].append({"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "action": "buy",
                     "ticker": ticker, "shares": shares, "price": price})


def apply_recommendation_cash(p, amount, spent):
    cash = p.get("cash", 0.0)
    if cash >= spent:
        p["cash"] = round(cash - spent, 2)
    else:
        p["cash"] = round(cash + amount - spent, 2)


def record_sale(p, ticker, shares=None, price=None):
    ticker = ticker.upper().replace(".", "-")
    h = p["holdings"].get(ticker)
    if not h:
        raise ValueError(f"You don't have any {ticker} saved in the portfolio.")
    sell = h["shares"] if shares is None else min(int(shares), h["shares"])
    if sell <= 0:
        raise ValueError("Number of shares to sell must be positive.")
    h["shares"] -= sell
    if h["shares"] == 0:
        del p["holdings"][ticker]
    if price:
        p["cash"] = round(p.get("cash", 0.0) + sell * float(price), 2)
    p["log"].append({"date": datetime.now().strftime("%Y-%m-%d %H:%M"), "action": "sell",
                     "ticker": ticker, "shares": sell, "price": price})
    return sell


MARKER_NAME = "reset_done.txt"
LOG_NAME = "review_log.csv"


def log_path():
    return os.path.join(os.path.dirname(FILE), LOG_NAME)


def append_review_log(rows):
    path = log_path()
    is_new = not os.path.exists(path)
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if is_new:
            w.writerow(["Checked at", "Ticker", "Shares", "Bought at", "Now", "Profit/Loss %", "Score", "Verdict"])
        for r in rows:
            w.writerow([when, r["ticker"], r["shares"], round(r["avg_cost"], 2), round(r["price"], 2),
                        round(r["pl_pct"], 1) + 0.0, r["score"], r["verdict"]])
    return path


def auto_reset(start_date):
    if not start_date:
        return None
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d").date()
    except ValueError:
        return f"EXPERIMENT_START '{start_date}' isn't a valid date (use YYYY-MM-DD like 2026-10-19), so I skipped the auto-reset."
    if datetime.now().date() < start:
        return None

    base = os.path.dirname(FILE)
    marker = os.path.join(base, MARKER_NAME)
    if os.path.exists(marker):
        with open(marker, "r", encoding="utf-8") as f:
            if f.read().strip() == start_date:
                return None

    backup = None
    if os.path.exists(FILE):
        backup = f"portfolio_practice_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
        os.replace(FILE, os.path.join(base, backup))
    log_backup = None
    if os.path.exists(log_path()):
        log_backup = f"review_log_practice_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
        os.replace(log_path(), os.path.join(base, log_backup))
    for name in ("scan_cache.json", "headlines_report.html", "latest_review.json"):
        path = os.path.join(base, name)
        if os.path.exists(path):
            os.remove(path)
    for name in os.listdir(base):
        if name.startswith("chart_") and name.endswith(".html"):
            os.remove(os.path.join(base, name))
    with open(marker, "w", encoding="utf-8") as f:
        f.write(start_date)

    msg = f"Fresh start! The experiment begins {start_date}, so I cleared the old scan and headlines."
    if backup:
        msg += f" Your practice portfolio was saved as {backup}."
    if log_backup:
        msg += f" Your practice review log was saved as {log_backup}."
    return msg
