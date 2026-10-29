import os
import sys
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.chdir(HERE)

INCLUDE_NEW_IDEAS = True

LOG = os.path.join(HERE, "daily_review.log")


def log(text):
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {text}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    log("starting scheduled review")
    try:
        import portfolio
        import review
        from engine import Engine

        pf = portfolio.load()
        if not pf["holdings"]:
            log("nothing in the portfolio yet, so there is nothing to review")
            return
        result = review.run_review(Engine(), pf, log, include_new_ideas=INCLUDE_NEW_IDEAS)
        if result.get("error"):
            log("review problem: " + result["error"])
            return
        review.save_latest(result)
        try:
            portfolio.append_review_log(result["rows"])
        except Exception as e:
            log(f"couldn't add to review_log.csv: {e}")
        log(f"done: {len(result['rows'])} stocks reviewed, {len(result['swaps'])} swap idea(s). Saved for the app.")
    except Exception:
        log("ERROR:\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
