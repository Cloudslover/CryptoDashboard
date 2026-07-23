# CryptoDashboard (fork of BTC AI Brain)

This repository contains the BTC AI Brain and dashboard for analyzing Bitcoin market data, computing technical indicators, deriving multi-timeframe signals, collecting macro & news data, making rule-based AI decisions, running backtests, and displaying results in a Dash dashboard.

This update improves reliability, logging, concurrency, retries, DB durability, and adds CI + basic tests.

Quickstart

1. Create a Python virtual environment and activate it:

   python -m venv venv
   venv\Scripts\activate  # Windows

2. Install dependencies:

   pip install -r requirements.txt

3. Copy environment variables (.env) and set values, for example:

   FRED_API_KEY=your_api_key_here

4. Run the app:

   python main.py

What's changed in this PR

- Add structured logging configuration.
- Use a requests Session with retries for external HTTP calls.
- Reduce blocking by parallelizing independent fetches in the dashboard.
- Add SQLite WAL mode for better concurrency.
- Replace ad-hoc prints with logging where components are instantiated.
- Move hard-coded API keys to environment variables.
- Add a basic CI workflow (pytest) and a simple unit test for indicators.

License: MIT
