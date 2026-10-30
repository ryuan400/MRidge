# MRidge

MRidge is a desktop chat app that helps you pick S&P 500 stocks. Tell it how much you want to invest, and it scores every stock on its price trend, RSI, and the mood of recent news headlines, then splits your money across the best-scoring ones.

After you buy, it remembers your portfolio and can tell you what to keep, sell, or buy more of, suggest swaps, and show price charts. The chat is powered by Claude, so you'll need your own Anthropic API key.

This is a personal passion project I've been doing, so PLEASE don't trust it completely.

## How it scores stocks

- **Price trend:** is the price above its 20-day average?
- **RSI:** is the stock oversold (might bounce) or overbought (might be overheated)?
- **News:** a finance model (FinBERT) reads recent headlines and rates them good, bad, or neutral.

These combine into one score: 60% price signals, 40% news. Stocks scoring above 0.3 get picked, and the money is split in proportion to each stock's score.

## What you need

- Windows
- [Python 3.12](https://www.python.org/downloads/). It has to be 3.12, because the launchers run `py -3.12`.
- An Anthropic API key from https://console.anthropic.com

## Setup

1. Download this project (green **Code** button, then **Download ZIP**) and unzip it.
2. Open a terminal in the folder and install the libraries:
   ```
   py -3.12 -m pip install -r requirements.txt
   ```
   This can take a while, because `torch` and `transformers` are big.
3. Make a new file in the folder called `api_key.txt` and paste only your API key into it, with no spaces, quotes, or extra lines. See `api_key.example.txt`.

## Running it

- Double-click `start.bat`, or
- Run `make_shortcut.bat` once to put an MRidge icon on your desktop, then use that.

Then just chat, for example: "I want to invest $5,000", "why did you pick that one?", "show me the headlines", or "should I sell anything?"

## Heads up: the first scan is slow

The first time you ask for a recommendation, MRidge downloads the FinBERT model (about 400 MB) and then scans the whole S&P 500: prices plus news headlines for every stock. That can take several minutes. Results are saved for 4 hours, so questions after that are quick.

## Optional: daily automatic review

MRidge can review your portfolio by itself every weekday:

```
py -3.12 schedule_review.py 16:30      runs a review every weekday at 4:30 PM
py -3.12 schedule_review.py --remove   turns it off
py -3.12 schedule_review.py --status   shows whether it's set up
```

Your PC needs to be on, and you need to be logged in, at that time. If it was off, the review runs as soon as the PC is back on. The results pop up the next time you open the app.

## Settings you can change

- **Look:** set `USE_MODERN_UI = False` in `app.py` for the classic look.
- **Colors:** change `ACCENT` in `modern_ui.py`.
- **Strategy:** `THRESHOLD`, `CACHE_HOURS`, and `NEWS_CANDIDATES` are at the top of `engine.py`.

## Note

This is a simple short-term strategy and it hasn't been backtested. It's a personal project, not financial advice.

Also, you may need to wait a little bit after you start the desktop version (the make_shortcut.bat one).
