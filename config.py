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
# Prediction-market (Polymarket) sentiment / macro-intelligence polling.
POLYMARKET_REFRESH_SECONDS = int(os.getenv("POLYMARKET_REFRESH_SECONDS", "900"))
# Horizon (hours) used to compute an implied-probability "shift since ~1 day ago".
POLYMARKET_SHIFT_HOURS = float(os.getenv("POLYMARKET_SHIFT_HOURS", "24.0"))
# Optional comma-separated list of specific Polymarket slugs to always include,
# in addition to keyword discovery. Leave empty to rely on keyword search.
POLYMARKET_SLUGS = os.getenv("POLYMARKET_SLUGS", "")
# Order-flow (CVD): number of recent aggregated trades to analyse per refresh.
CVD_TRADES_LIMIT = int(os.getenv("CVD_TRADES_LIMIT", "1000"))
MAX_RISK_PERCENT = float(os.getenv("MAX_RISK_PERCENT", "1.0"))
MIN_RISK_REWARD = float(os.getenv("MIN_RISK_REWARD", "1.5"))

# ── Brain Memory & Signal Stability (anti-whipsaw) ────────────────────
SIGNAL_COOLDOWN_MINUTES = int(os.getenv("SIGNAL_COOLDOWN_MINUTES", "30"))
FLIP_PRICE_THRESHOLD_PCT = float(os.getenv("FLIP_PRICE_THRESHOLD_PCT", "0.8"))
MIN_CONF_TO_FLIP = float(os.getenv("MIN_CONF_TO_FLIP", "75"))
SAME_THESIS_PCT = float(os.getenv("SAME_THESIS_PCT", "0.3"))
MAX_FLIPS_PER_HOUR = int(os.getenv("MAX_FLIPS_PER_HOUR", "2"))

# ── Portfolio Guardian (fund protection) ───────────────────────────────
MAX_TOTAL_RISK_PERCENT = float(os.getenv("MAX_TOTAL_RISK_PERCENT", "3.0"))
MAX_OPEN_TRADES = int(os.getenv("MAX_OPEN_TRADES", "2"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3.0"))
MAX_LEVERAGE = int(os.getenv("MAX_LEVERAGE", "10"))

# ── LLM Assistant (AI Brain market-intelligence brief) ─────────────────
# auto | groq | gemini | off — auto tries Groq first, then Gemini.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "auto")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# openai/gpt-oss-120b is Groq's recommended replacement for
# llama-3.3-70b-versatile, which sunsets 2026-08-16.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
# Gemini free tier (since Apr 2026) covers Flash / Flash-Lite models only.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
LLM_REFRESH_SECONDS = int(os.getenv("LLM_REFRESH_SECONDS", "180"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "900"))
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "20"))

TIMEFRAMES = {
    "1m": 500, "5m": 500, "15m": 500, "1h": 500,
    "4h": 500, "1d": 730, "1w": 520,
}
