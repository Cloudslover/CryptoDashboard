# BTC AI Brain — human-in-the-loop Bitcoin futures research

A local, **decision-support** dashboard for Bitcoin futures research. It brings together BTC market data, technical and multi-timeframe context, derivatives metrics, macro markets, public RSS news, local persistence, and a reproducible technical backtest.

> **Important:** This application does not hold exchange credentials, does not include an order-execution client, and cannot place a live order. “Approve” records a **paper trade plan** only. BTC futures are high-risk; no model, indicator, sentiment score, or backtest is a guarantee of a trade outcome.

## What is included

- **Market data:** Binance public BTC futures OHLCV, ticker, funding, open interest, order-book imbalance, fear & greed.
- **BTC Futures Command Center:** a focused OSINT workflow distilled from the broad OSINT4all directory: breaking-news feeds, macro/Fed/BLS/CME calendar links, institutional/ETF research links, sentiment links, derivatives positioning, on-chain network activity, a clearly labelled large-mempool transaction watch, and Binance Futures public API health.
- **Technical context:** EMA 21/50/200, RSI, MACD, ATR, Bollinger Bands, Supertrend, support/resistance, signal table, and 1m–1w multi-timeframe consensus.
- **Macro watch:** US equities (S&P 500, Nasdaq, Dow), London (FTSE 100), Europe (DAX), Japan (Nikkei), VIX, DXY, EUR/USD, USD/JPY, gold and oil. Each instrument is fetched independently, so a broken feed degrades only that instrument.
- **News/event watch:** public crypto, Fed, macro, and geopolitical RSS sources. The last successful feed remains visible during outages. Social/influencer data is intentionally not scraped; use a licensed API and add it as a source if required.
- **Risk-first plans:** explainable rule-based long/short/stand-aside recommendations with entry reference, invalidation, stop, targets, confidence, and explicit human approval queue.
- **Storage:** SQLite WAL database persists fetched OHLCV candles, snapshots, news, macro data, and plans in `btc_brain.db` (ignored by Git).
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

The new command-center panels are intentionally arranged as a research funnel:

1. **Breaking news:** read the timestamped RSS items and open the original source before treating a headline as fact.
2. **Macro calendar:** check the linked official Fed and BLS release calendars and CME FedWatch for scheduled volatility; links are not a promise that an event will move BTC.
3. **Derivatives:** compare funding, open interest, long/short positioning, and order-book imbalance. A crowded direction can amplify a move, but none of these fields predicts it alone.
4. **On-chain and whale watch:** use the block height, mempool load, and large pending transaction watch as network context. A mempool transaction has no known sender/receiver attribution and is **not** an exchange inflow/outflow signal; verify it in an explorer.
5. **Institutional and sentiment:** use SEC EDGAR, ETF-flow research, Fear & Greed, Google Trends, and Reddit as leads. Social sentiment is noisy and is not fact checking.
7. Start with the 1D/4H trend and macro/event context—not a 1m signal.
8. Review the proposed plan’s **invalidation**, stop distance, target, funding, and news risk.
9. Set your own position size from a fixed account-risk budget (the default suggested cap is 1%); do not treat the displayed leverage as a recommendation.
10. Approve only after independently checking price and upcoming economic events. Approval is a local paper-plan record.
11. Run a backtest only over sufficiently long, representative data. Include fees/slippage and reject strategies that fail out-of-sample testing.

## Data quality and limitations

Correlation is conditional: e.g. BTC may trade like a risk asset one period and a safe-haven narrative another. Keyword news sentiment is explainable but cannot validate a story, detect misinformation, or predict policy outcomes. The tool is designed to make uncertainty visible, not to make autonomous trading decisions.

## Development

```bash
python -m compileall .
pytest -q
```

The project runs CI on pull requests. Git version control tracks source and tests; local databases and secrets are excluded through `.gitignore`.
