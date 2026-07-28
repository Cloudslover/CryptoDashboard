"""Local, dependency-light technical indicators used by the dashboard and backtests."""
from __future__ import annotations
import numpy as np
import pandas as pd


class TechnicalIndicators:
    @staticmethod
    def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame() if df is None else df.copy()
        out = df.copy()
        for period in (9, 21, 50, 100, 200):
            out[f"ema_{period}"] = out.close.ewm(span=period, adjust=False).mean()
        for period in (20, 50, 200):
            out[f"sma_{period}"] = out.close.rolling(period).mean()
        out["volume_ma"] = out.volume.rolling(20).mean()
        out["volume_ratio"] = out.volume / out.volume_ma.replace(0, np.nan)

        delta = out.close.diff()
        gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
        rs = gain.ewm(alpha=1 / 14, adjust=False).mean() / loss.ewm(alpha=1 / 14, adjust=False).mean().replace(0, np.nan)
        out["rsi"] = 100 - 100 / (1 + rs)

        fast = out.close.ewm(span=12, adjust=False).mean()
        slow = out.close.ewm(span=26, adjust=False).mean()
        out["macd"] = fast - slow
        out["macd_signal"] = out.macd.ewm(span=9, adjust=False).mean()
        out["macd_hist"] = out.macd - out.macd_signal

        tr = pd.concat([out.high - out.low, (out.high - out.close.shift()).abs(), (out.low - out.close.shift()).abs()], axis=1).max(axis=1)
        out["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
        out["atr_pct"] = out.atr / out.close * 100
        mid, std = out.close.rolling(20).mean(), out.close.rolling(20).std()
        out["bb_mid"], out["bb_upper"], out["bb_lower"] = mid, mid + 2 * std, mid - 2 * std

        # Supertrend implementation deliberately uses explicit arrays to prevent look-ahead bias.
        hl2 = (out.high + out.low) / 2
        upper, lower = hl2 + 3 * out.atr, hl2 - 3 * out.atr
        final_upper, final_lower = upper.copy(), lower.copy()
        bull = np.ones(len(out), dtype=bool)
        for i in range(1, len(out)):
            final_upper.iloc[i] = upper.iloc[i] if upper.iloc[i] < final_upper.iloc[i - 1] or out.close.iloc[i - 1] > final_upper.iloc[i - 1] else final_upper.iloc[i - 1]
            final_lower.iloc[i] = lower.iloc[i] if lower.iloc[i] > final_lower.iloc[i - 1] or out.close.iloc[i - 1] < final_lower.iloc[i - 1] else final_lower.iloc[i - 1]
            bull[i] = out.close.iloc[i] >= final_lower.iloc[i] if bull[i - 1] else out.close.iloc[i] > final_upper.iloc[i]
        out["supertrend_bull"] = bull
        out["supertrend"] = np.where(bull, final_lower, final_upper)
        return out

    @staticmethod
    def support_resistance(df: pd.DataFrame, lookback: int = 120) -> dict:
        data = df.tail(lookback)
        if data.empty:
            return {"support": None, "resistance": None}
        price = float(data.close.iloc[-1])
        lows = data.low[(data.low.shift(2) > data.low) & (data.low.shift(-2) > data.low)]
        highs = data.high[(data.high.shift(2) < data.high) & (data.high.shift(-2) < data.high)]
        supports = lows[lows < price]
        resistances = highs[highs > price]
        return {
            "support": float(supports.iloc[-1]) if not supports.empty else float(data.low.min()),
            "resistance": float(resistances.iloc[-1]) if not resistances.empty else float(data.high.max()),
        }
