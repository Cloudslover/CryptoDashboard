"""Explainable market-cycle score: low is accumulation, high is distribution/markdown risk."""
from __future__ import annotations
import numpy as np


class MarketCycleAnalyzer:
    def calculate_cycle_score(self, df, funding, oi, fear_greed, ls_ratio):
        if df is None or df.empty:
            return {"score": 5.0, "phase": "Unknown", "caution": "WAIT", "description": "Awaiting price history."}
        last = df.iloc[-1]
        score = 5.0
        if "ema_200" in df and np.isfinite(last.ema_200):
            score += 1.3 if last.close > last.ema_200 else -1.3
        if "rsi" in df and np.isfinite(last.rsi):
            score += (float(last.rsi) - 50) / 18
        fg = float(fear_greed.get("value", 50))
        score += (fg - 50) / 25
        rate = float(funding.get("current", 0))
        score += min(1.5, max(-1.5, rate / .02))
        score += min(1, max(-1, float(oi.get("change_24h", 0)) / 10))
        ratio = float(ls_ratio.get("long_short_ratio", 1))
        score += .7 if ratio > 1.5 else -.7 if ratio < .7 else 0
        score = round(float(np.clip(score, 1, 10)), 1)
        if score <= 3:
            phase, caution = "Accumulation", "OPPORTUNITY"
        elif score <= 5.5:
            phase, caution = "Markup", "BULLISH"
        elif score <= 7.5:
            phase, caution = "Distribution", "CAUTION"
        else:
            phase, caution = "Markdown", "DANGER"
        return {"score": score, "phase": phase, "caution": caution,
                "description": "Rule-based cycle score; use it as context, not a trading command."}
