# BTC AI Brain — human-in-the-loop Bitcoin futures research

A local, **decision-support** dashboard for Bitcoin futures research. It brings together BTC market data, technical and multi-timeframe context, derivatives metrics, macro markets, public RSS news, local persistence, and a reproducible technical backtest.

> **Important:** This application does not hold exchange credentials, does not include an order-execution client, and cannot place a live order. “Approve” records a **paper trade plan** only. BTC futures are high-risk; no model, indicator, sentiment score, or backtest is a guarantee of a trade outcome.

## What is included

- **Market data:** Binance public BTC futures OHLCV, ticker, funding, open interest, order-book imbalance, fear & greed.
- **BTC Futures Command Center:** a focused OSINT workflow distilled from the broad OSINT4all directory: breaking-news feeds, macro/Fed/BLS/CME calendar links, institutional/ETF research links, sentiment links, derivatives positioning, on-chain network activity, a clearly labelled large-mempool transaction watch, and Binance Futures public API health.
- **Technical context:** EMA 21/50/200, RSI, MACD, ATR, Bollinger Bands, Supertrend, support/resistance, signal table, and 1m–1w multi-timeframe consensus.
- **Macro watch:** US equities (S&P 500, Nasdaq, Dow), London (FTSE 100), Europe (DAX), Japan (Nikkei), VIX, DXY, EUR/USD, USD/JPY, gold and oil. Each instrument is fetched independently, so a broken feed degrades only that instrument.
- **News/event watch:** public crypto, Fed, macro, and geopolitical RSS sources. The last successful feed remains visible during outages. Social/influencer data is intentionally not scraped; use a licensed API and add it as a source if required.
- **Prediction markets (Polymarket):** real-money implied-probability sentiment for crypto and macro events (Fed cuts, CPI, recession odds, Bitcoin price targets, ETF/reserve milestones) via the public Gamma API. The dashboard tracks how each market's "P(Yes)" moves over time (e.g. "shift since ~1 day ago"), flags the biggest probability shifts, and correlates them with BTC price direction. Like every other source it degrades gracefully during outages and retains the last snapshot.
- **Order flow (CVD):** Cumulative Volume Delta computed from recent Binance futures aggregated trades, shown as a signal plus a dedicated panel (net delta, buy/sell split, buy pressure, and a price/delta *divergence* flag).
- **Trade Quality scoring:** the dashboard scores how strong the *evidence* is for the current setup across 10 independent factors (Market Structure, Trend, Volume, Funding, Open Interest, Whale Activity, News Risk, Macro Alignment, Sentiment, Risk/Reward) and rolls them into an overall **Trade Quality score /100** with per-factor reasons. It tells you how strong the case is, not what to do.
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

## Prediction-market (Polymarket) layer

Polymarket prices are **implied probabilities from capital at risk** — a consensus view of what informed participants expect. This app uses them as a sentiment / macro-intelligence dashboard, **not** a buy/sell signal:

- **Track shifts, not levels:** each market records its "P(Yes)" to the local SQLite DB. The dashboard shows the change since a configurable horizon (default ~24 h), so you see repricing *momentum* rather than just a number.
- **Biggest shifts:** the markets with the largest absolute probability moves are surfaced so you can investigate *why* the crowd repriced.
- **BTC correlation:** the aggregate crypto-probability move is compared with BTC's 24 h change and labelled "aligned" or "conflict" (e.g. probabilities moved faster than price).
- **Into the AI:** prediction-market bias contributes a small, explainable factor to the recommendation reasoning (e.g. "PREDICTION MKTS: BULLISH bias (crypto +8.0pt)").

Configuration (`.env`):

- `POLYMARKET_REFRESH_SECONDS` — polling interval (default `900`).
- `POLYMARKET_SHIFT_HOURS` — horizon for computing probability shifts (default `24`).
- `POLYMARKET_SLUGS` — optional comma-separated specific market slugs to always include, on top of automatic keyword discovery.

> Note: Polymarket is a *context* layer. Combine it with technicals, macro, and on-chain metrics; never trade on a probability move alone.

## Order-flow (CVD) layer

CVD ("cumulative volume delta") is computed from Binance futures `aggTrades`: each trade is classified by whether the buyer was the taker, summed into buy vs sell volume, and expressed as a net signed delta. It is an order-flow *context* layer:

- **Net delta / buy pressure** — who's been the aggressor recently.
- **Price/delta divergence** — a flag when price moved one way but delta moved the other, a classic absorption/climax warning.
- **CVD signal** — appears in the signals table (Signal #11) alongside funding, OI, and order-book imbalance.

Configuration (`.env`): `CVD_TRADES_LIMIT` (default `1000`) controls how many recent trades are analysed per refresh.

> CVD from a single short window is a snapshot, not a full session footprint. Treat it as one confirming/disconfirming input, never as a standalone entry trigger.

## Trade Quality layer

The AI no longer just says "buy/sell/stand aside" — it scores **how strong the evidence is**. For each setup it evaluates 10 factors (each 0-10, with a plain-language reason) and combines them by weight into an overall **Trade Quality score /100**:

| Factor | Weight |
| --- | ---: |
| Market Structure | 14% |
| Trend | 13% |
| Risk/Reward | 11% |
| Funding | 10% |
| Macro Alignment | 10% |
| Volume | 9% |
| News Risk | 9% |
| Open Interest | 8% |
| Whale Activity | 8% |
| Sentiment | 8% |

The breakdown is shown in a dedicated panel, stored with each plan, and included in the approval queue. Weights live in `brain/trade_quality.py` if you want to tune them.

> The score is a structured, auditable evidence summary — not a prediction and not a recommendation. Set your own position size and do your own risk management.

## LLM assistant (AI Brain brief)

An optional **AI BRAIN — Market Intelligence** panel at the top of the dashboard asks an external LLM to *read every panel* (price, cycle, macro, derivatives, CVD order flow, signals, news, Polymarket, the local rule-based strategy read, and the Trade Quality breakdown) and write a **plain-English brief** in fixed sections: Market Summary → Derivatives → Order Flow → Trade Quality → Risk Assessment → Conflicts → What to Watch → Bottom Line.

- **Free keys, automatic fallback.** `LLM_PROVIDER=auto` tries **Groq** first (free key: [console.groq.com](https://console.groq.com)) and falls back to **Google Gemini** (free key: [aistudio.google.com](https://aistudio.google.com)) if Groq is missing or errors. Set `LLM_PROVIDER=groq`, `gemini`, or `off` to pin a behaviour.
- **Explainable by design.** The system prompt forbids buy/sell commands, order entries, position sizing, and leverage advice. The LLM *describes* the data and its conflicts; it never issues instructions and never touches the approval queue.
- **Never blocks the dashboard.** Briefs are generated on a background daemon thread behind a TTL cache (`LLM_REFRESH_SECONDS`, default `180`). With no key configured — or if all providers fail — the panel degrades to an explicit `ASSISTANT OFFLINE` notice with setup hints; a previous successful brief is retained as `STALE` instead of disappearing.

Configuration (`.env`):

- `LLM_PROVIDER` — `auto` (default), `groq`, `gemini`, or `off`.
- `GROQ_API_KEY` / `GEMINI_API_KEY` — **never commit real keys**; `.env` is gitignored.
- `GROQ_MODEL` — default `openai/gpt-oss-120b` (Groq's recommended replacement for `llama-3.3-70b-versatile`, which sunsets 2026-08-16).
- `GEMINI_MODEL` — default `gemini-2.5-flash` (the Gemini free tier covers Flash/Flash-Lite models only since April 2026).
- `LLM_REFRESH_SECONDS` / `LLM_MAX_TOKENS` — generation cadence and brief length cap.

Without any key the dashboard behaves exactly as before; the assistant is an add-on layer, not a dependency.

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

## Troubleshooting

**The dashboard takes many minutes (up to ~30) before the server appears.** The server is supposed to open in ~1–2 s; the first data snapshot streams in the background. If the terminal blocks for minutes instead, you are running an old copy of `main.py` that called `service.refresh()` synchronously before starting the web server. Update your local code — the startup is not gated on the data pipeline anymore.

**Data panels stay on BOOTING/DEGRADED when a provider is unreachable.** Third-party sources (mempool.space, blockchain.info, Yahoo Finance, Polymarket, RSS feeds) can be blocked, rate-limited, or slow on some networks. All endpoints now run concurrently with short timeouts and a per-host circuit breaker, so a snapshot completes in seconds and dead hosts are retried at most every 5 minutes. Binance itself is the only source the pipeline requires for a LIVE snapshot; if Binance is also unreachable, the last successful snapshot is retained.

**Repeated `Callback function not found for output '..status.children...'` errors (500) every ~60 s.** The browser tab is stale — it was opened against an older version of the dashboard (fewer panels), and it keeps polling an old callback signature. Close old tabs and hard-refresh the page (`Ctrl+Shift+R`), or restart the browser. A fresh page load always gets the current callback set.

**`L/S cols:` / `get_agg_trades unavailable` warnings.** These were bugs in parsing Binance's current response columns (`buySellRatio/buyVol/sellVol`, lowercase `p/q` agg-trade keys); they are fixed and the Long/Short and CVD panels now populate.

## Development

```bash
python -m compileall .
pytest -q
```

The project runs CI on pull requests. Git version control tracks source and tests; local databases and secrets are excluded through `.gitignore`.
