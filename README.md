# BTC AI Brain — human-in-the-loop Bitcoin futures research

A local, **decision-support** dashboard for Bitcoin futures research. It brings together BTC market data, technical and multi-timeframe context, derivatives metrics, macro markets, public RSS news, local persistence, and a reproducible technical backtest.

> **Important:** This application does not hold exchange credentials, does not include an order-execution client, and cannot place a live order. “Approve” records a **paper trade plan** only. BTC futures are high-risk; no model, indicator, sentiment score, or backtest is a guarantee of a trade outcome.

## What is included

- **Market data:** Binance public BTC futures OHLCV, ticker, funding, open interest, order-book imbalance, fear & greed.
- **Technical context:** EMA 21/50/200, RSI, MACD, ATR, Bollinger Bands, Supertrend, support/resistance, signal table, 1m–1w multi-timeframe consensus, and chart line/shape drawing tools.
- **Macro watch:** US equities (S&P 500, Nasdaq, Dow), London (FTSE 100), Europe (DAX), Japan (Nikkei), VIX, DXY, EUR/USD, USD/JPY, gold and oil. Each instrument is fetched independently, so a broken feed degrades only that instrument; an unavailable or zero VIX is excluded rather than treated as a bullish low-VIX reading.
- **News/event watch:** public crypto, Fed, macro, and geopolitical RSS sources. The last successful feed remains visible during outages. Social/influencer data is intentionally not scraped; use a licensed API and add it as a source if required.
- **Risk-first plans:** explainable rule-based long/short/stand-aside recommendations with invalidation and an explicit human approval queue. `STAND_ASIDE` intentionally shows no entry, stop, targets, size, or leverage.
- **Paper portfolio:** approve a queued plan, monitor directional live P&L, manually close it at a supplied or current market price, and review durable win/loss and performance records. These actions never place exchange orders.
- **Storage:** SQLite WAL persists fetched OHLCV candles, snapshots, news, macro data, and the complete paper-plan lifecycle in `btc_brain.db` (ignored by Git). Approved/open plans and closed results are restored after restart.
- **Backtesting:** a small no-look-ahead technical trend/pullback backtester including configurable fees and slippage. It evaluates a rule set, not future performance.

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Windows PowerShell: Copy-Item .env.example .env
python main.py
```

Open `http://127.0.0.1:8050`.

`FRED_API_KEY` is optional. Without it, the dashboard continues operating and marks official Fed observations unavailable. Public market/news providers can rate-limit, move, or fail; the dashboard retains the last successful data and marks degraded data instead of stopping.

## Operating workflow

1. Start with the 1D/4H trend and macro/event context—not a 1m signal.
2. Review the proposed plan’s **invalidation**, stop distance, target, funding, and news risk.
3. Set your own position size from a fixed account-risk budget (the default suggested cap is 1%); do not treat the displayed leverage as a recommendation.
4. Approve only after independently checking price and upcoming economic events. Approval opens a local paper-plan record that survives an application restart.
5. Monitor the Paper Portfolio and manually close the record when your paper exit occurs. Displayed P&L is the directional BTC price move before leverage, fees, slippage, and funding—not an account-balance estimate.
6. Run a backtest only over sufficiently long, representative data. Include fees/slippage and reject strategies that fail out-of-sample testing.

## Data quality and limitations

Correlation is conditional: e.g. BTC may trade like a risk asset one period and a safe-haven narrative another. Keyword news sentiment is explainable but cannot validate a story, detect misinformation, or predict policy outcomes. The tool is designed to make uncertainty visible, not to make autonomous trading decisions.

## Development

```bash
python -m compileall .
pytest -q
```

The project runs CI on pull requests. Git version control tracks source and tests; local databases and secrets are excluded through `.gitignore`.
