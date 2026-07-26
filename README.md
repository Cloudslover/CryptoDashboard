# CryptoDashboard (fork of BTC AI Brain)

This repository contains the BTC AI Brain and dashboard for analyzing Bitcoin market data, computing technical indicators, deriving multi-timeframe signals, collecting macro & news data, making rule-based AI decisions, running backtests, and displaying results in a Dash dashboard.

This update improves reliability, logging, concurrency, retries, DB durability, and adds CI + basic tests.

Quickstart

1. Create a Python virtual environment and activate it:

   python -m venv venv
   venv\Scripts\activate  # Windows

2. Install dependencies:

   pip install -r requirements.txt

3. Create a .env file (copy from .env.example) and set environment variables. Example:

   FRED_API_KEY=your_fred_api_key_here

   # Create .env from the example (Windows PowerShell):
   cp .env.example .env
   # Edit .env and add your keys. Do NOT commit .env to the repository.

4. Run the app (recommended):

   python start.py

   start.py will load .env (via python-dotenv) and then execute main.py so environment
   variables are available to your app. If your project uses a different entrypoint,
   either run it directly or modify start.py accordingly.

What's changed in this PR

- Add structured logging configuration.
- Use a requests Session with retries for external HTTP calls.
- Reduce blocking by parallelizing independent fetches in the dashboard.
- Add SQLite WAL mode for better concurrency.
- Replace ad-hoc prints with logging where components are instantiated.
- Move hard-coded API keys to environment variables.
- Add a basic CI workflow (pytest) and a small unit test suite.

License: MIT
