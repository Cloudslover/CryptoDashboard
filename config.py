"""Application configuration. Copy .env.example to .env to override settings."""
from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()

EXCHANGE = os.getenv("EXCHANGE", "binance")
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
FUTURES_SYMBOL = os.getenv("FUTURES_SYMBOL", "BTC/USDT:USDT")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8050"))
REFRESH_SECONDS = int(os.getenv("REFRESH_SECONDS", "60"))
NEWS_REFRESH_SECONDS = int(os.getenv("NEWS_REFRESH_SECONDS", "300"))
MACRO_REFRESH_SECONDS = int(os.getenv("MACRO_REFRESH_SECONDS", "300"))
MAX_RISK_PERCENT = float(os.getenv("MAX_RISK_PERCENT", "1.0"))
MIN_RISK_REWARD = float(os.getenv("MIN_RISK_REWARD", "1.5"))

TIMEFRAMES = {
    "1m": 500, "5m": 500, "15m": 500, "1h": 500,
    "4h": 500, "1d": 730, "1w": 520,
}
