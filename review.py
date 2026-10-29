import copy
import json
import os
import time
from datetime import datetime

import engine as eng
import portfolio
from engine import MAX_SWAPS, SELL_BELOW, SWAP_GAP, THRESHOLD

LATEST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "latest_review.json")


def compute_swaps(held, targets, cash, gap=SWAP_GAP, max_swaps=MAX_SWAPS, buy_floor=THRESHOLD):
    swaps, used_sell, used_buy = [], set(), set()
    cash_left = cash
    ranked_targets = sorted(targets, key=lambda t: -targets[t]["score"])
    for w in sorted(held, key=lambda t: held[t]["score"]):
        if len(swaps) >= max_swaps:
            break
        if w in used_buy:
            continue
        hw = held[w]
        for b in ranked_targets:
            tb = targets[b]
            if tb["score"] <= buy_floor or tb["score"] - hw["score"] < gap:
                break
            if b == w or b in used_sell or b in used_buy:
                continue
            proceeds = hw["shares"] * hw["price"]
            budget = proceeds + cash_left
            n = int(budget // tb["price"])
            if n < 1:
                continue
            cost = n * tb["price"]
            cash_left = budget - cost
            swaps.append({
                "sell": {"ticker": w, "shares": hw["shares"], "price": round(hw["price"], 2),
                         "proceeds": round(proceeds, 2), "score": hw["score"]},
                "buy": {"ticker": b, "shares": n, "price": round(tb["price"], 2), "cost": round(cost, 2),
                        "score": tb["score"], "already_owned": b in held},
                "score_gap": round(tb["score"] - hw["score"], 2),
                "cash_left_after": round(cash_left, 2),
            })
            used_sell.add(w)
            used_buy.add(b)
            break
    return swaps


def apply_swaps_to_portfolio(p, swaps):
    for sw in swaps:
        w, b = sw["sell"], sw["buy"]
        portfolio.record_sale(p, w["ticker"], w["shares"], w["price"])
        portfolio.add_purchase(p, b["ticker"], b["shares"], b["price"])
    return p


def market_scores(engine, say, fresh_scan=False):
    def fresh():
        return bool(engine.scores) and engine.scan_time and (time.time() - engine.scan_time) / 3600 <= eng.CACHE_HOURS

    if not fresh():
        engine.load_cache()
    if not fresh() and fresh_scan:
        say("Scanning the whole market for new ideas. This takes a few minutes...")
        engine.scan(say)
    return engine.scores if fresh() else {}


def run_review(engine, pf, say, include_new_ideas=False):
    holdings = pf["holdings"]
    if not holdings:
        return {"error": "Nothing is saved in the portfolio yet. The user needs to tell you what they bought first."}
    tickers = list(holdings)
    say(f"Checking your {len(tickers)} stocks with fresh prices and news...")
    scores, missing = engine.score_tickers(tickers, say)

    rows, out = [], []
    total_value = total_cost = sell_value = 0.0
    cash = pf.get("cash", 0.0)
    for t in tickers:
        h, d = holdings[t], scores.get(t)
        if not d:
            continue
        c = d["composite"]
        strong = c > THRESHOLD
        if c < SELL_BELOW:
            verdict = "SELL"
            sell_value += d["price"] * h["shares"]
        elif strong and cash >= d["price"]:
            verdict = "BUY MORE"
        else:
            verdict = "KEEP"
        pl_pct = (d["price"] / h["avg_cost"] - 1) * 100
        total_value += d["price"] * h["shares"]
        total_cost += h["avg_cost"] * h["shares"]
        rows.append({"ticker": t, "shares": h["shares"], "avg_cost": h["avg_cost"], "price": d["price"],
                     "pl_pct": pl_pct, "score": c, "verdict": verdict})
        out.append({
            "ticker": t, "verdict": verdict, "shares": h["shares"], "bought_at": h["avg_cost"],
            "price_now": round(d["price"], 2), "profit_loss_pct": round(pl_pct, 1),
            "still_strong": strong, "composite": c, "technical": d["tech"], "news_sentiment": d["sentiment"],
            "rsi": round(d["rsi"], 1), "sma20": round(d["sma20"], 2),
            "headline_count": len(d["headlines"]),
            "sample_headlines": [{"title": x["title"], "label": x["label"].lower()} for x in d["headlines"][:3]],
        })

    market = market_scores(engine, say, fresh_scan=include_new_ideas)
    targets = {t: {"price": d["price"], "score": d["composite"]} for t, d in market.items() if t not in holdings}
    for t, d in scores.items():
        targets[t] = {"price": d["price"], "score": d["composite"]}
    held = {r["ticker"]: {"shares": r["shares"], "price": r["price"], "score": r["score"]} for r in rows}
    swaps = compute_swaps(held, targets, cash)
    rows_by = {r["ticker"]: r for r in rows}
    out_by = {o["ticker"]: o for o in out}
    for sw in swaps:
        for t, v in ((sw["sell"]["ticker"], "SWAP OUT"), (sw["buy"]["ticker"], "BUY MORE")):
            if t in rows_by and not (v == "SWAP OUT" and rows_by[t]["verdict"] == "SELL"):
                rows_by[t]["verdict"] = v
                out_by[t]["verdict"] = v

    order = {"SELL": 0, "SWAP OUT": 1, "BUY MORE": 2, "KEEP": 3}
    rows.sort(key=lambda r: (order[r["verdict"]], -r["score"]))
    out.sort(key=lambda r: (order[r["verdict"]], -r["composite"]))

    now = datetime.now()
    when = now.strftime("%b %d, %I:%M %p")
    portfolio_payload = None
    if rows:
        portfolio_payload = {
            "rows": rows, "total_value": total_value, "total_cost": total_cost,
            "total_pl": total_value - total_cost,
            "total_pl_pct": (total_value / total_cost - 1) * 100 if total_cost else 0.0,
            "cash": cash, "swaps": swaps, "missing": missing, "when": when,
        }

    after_payload, after_info = None, {}
    if swaps and rows:
        work = copy.deepcopy(pf)
        apply_swaps_to_portfolio(work, swaps)
        after_rows, stocks_after = [], 0.0
        for t, h in work["holdings"].items():
            d = targets.get(t)
            if not d:
                continue
            before = holdings.get(t)
            status = "NEW" if not before else ("ADDED" if h["shares"] > before["shares"] else "KEEP")
            stocks_after += d["price"] * h["shares"]
            after_rows.append({"ticker": t, "shares": h["shares"], "avg_cost": h["avg_cost"], "price": d["price"],
                               "pl_pct": (d["price"] / h["avg_cost"] - 1) * 100, "score": d["score"],
                               "verdict": status})
        after_rows.sort(key=lambda r: -r["score"])
        cash_after = work.get("cash", 0.0)
        after_payload = {
            "rows": after_rows, "swaps": swaps, "stocks_value": stocks_after, "cash": cash_after,
            "total_after": stocks_after + cash_after, "total_before": total_value + cash,
        }
        after_info = {"portfolio_after_swaps": {"stocks_value": round(stocks_after, 2),
                                                "cash": round(cash_after, 2),
                                                "total": round(stocks_after + cash_after, 2),
                                                "total_before_swaps": round(total_value + cash, 2)}}

    return {
        "timestamp": time.time(), "when": when,
        "rows": rows, "out": out, "swaps": swaps, "missing": missing, "cash": cash,
        "total_value": total_value, "total_cost": total_cost, "sell_value": sell_value,
        "checked_market": bool(market),
        "portfolio_payload": portfolio_payload, "after_payload": after_payload, "after_info": after_info,
        "review_scores": scores,
    }


def claude_view(result):
    cash, sell_value = result["cash"], result["sell_value"]
    return {
        **result["after_info"],
        "holdings": result["out"], "couldnt_get_data_for": result["missing"],
        "total_value_now": round(result["total_value"], 2), "total_paid": round(result["total_cost"], 2),
        "cash_available": round(cash, 2), "cash_freed_if_the_sells_were_sold": round(cash + sell_value, 2),
        "swap_ideas": result["swaps"],
        "swap_note": (f"Swaps only appear when a stronger stock (score above {THRESHOLD}) beats a holding by at least "
                      f"{SWAP_GAP}, and the sale proceeds plus cash can buy at least one whole share. "
                      + ("New market ideas were included." if result["checked_market"] else
                         "No recent market scan was available, so only stocks the user already owns were considered "
                         "as swap targets. Offer to scan the market for new ideas if they want that.")),
        "rule": (f"score below {SELL_BELOW} = SELL. At or above {SELL_BELOW} = KEEP. A score above {THRESHOLD} means it still "
                 f"looks strong (still_strong), and it is labeled BUY MORE only if the portfolio has cash for another share; "
                 f"otherwise it is just a strong KEEP."),
        "note": ("The review table is already shown in a separate window. If there are swap ideas, a second window "
                 "shows the portfolio as it would look after the swaps, with a button to save them. Don't repeat the tables."),
    }


def save_latest(result):
    data = {"timestamp": result["timestamp"], "when": result["when"], "shown": False, "result": result}
    tmp = LATEST_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, LATEST_FILE)


def load_latest():
    if not os.path.exists(LATEST_FILE):
        return None
    try:
        with open(LATEST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def mark_shown():
    data = load_latest()
    if data:
        data["shown"] = True
        tmp = LATEST_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, LATEST_FILE)
