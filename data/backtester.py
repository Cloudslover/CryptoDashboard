"""Small no-look-ahead backtester for evaluating the bundled technical setup."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from data.indicators import TechnicalIndicators

@dataclass
class BacktestResult:
    strategy: str; total_trades: int; win_rate: float; total_return: float; max_drawdown: float; profit_factor: float; trades: list

class Backtester:
    def __init__(self, initial_capital=10_000, fee_rate=.0004, slippage=.0002):
        self.initial_capital, self.fee_rate, self.slippage = initial_capital, fee_rate, slippage

    def run_strategy(self, candles, strategy="trend_pullback", risk_per_trade=.01):
        df = TechnicalIndicators.add_all_indicators(candles)
        equity, curve, trade, trades = self.initial_capital, [self.initial_capital], None, []
        for i in range(201, len(df)):
            row, prev = df.iloc[i], df.iloc[i-1]
            price = float(row.close)
            if trade:
                hit_stop = row.low <= trade["stop"] if trade["side"] == "LONG" else row.high >= trade["stop"]
                hit_target = row.high >= trade["target"] if trade["side"] == "LONG" else row.low <= trade["target"]
                flipped = bool(row.supertrend_bull) != (trade["side"] == "LONG")
                if hit_stop or hit_target or flipped:
                    exit_price = trade["stop"] if hit_stop else trade["target"] if hit_target else price
                    direction = 1 if trade["side"] == "LONG" else -1
                    pnl = direction * (exit_price - trade["entry"]) * trade["qty"] - (trade["entry"] + exit_price) * trade["qty"] * self.fee_rate
                    equity += pnl; trades.append({**trade, "exit":exit_price, "pnl":pnl, "reason":"STOP" if hit_stop else "TARGET" if hit_target else "TREND_FLIP"}); trade = None
            if trade is None:
                long = bool(row.supertrend_bull) and row.close > row.ema_200 and 45 <= row.rsi <= 65 and row.macd > row.macd_signal and prev.rsi <= row.rsi
                short = not bool(row.supertrend_bull) and row.close < row.ema_200 and 35 <= row.rsi <= 55 and row.macd < row.macd_signal and prev.rsi >= row.rsi
                if long or short:
                    side, atr = ("LONG" if long else "SHORT"), max(float(row.atr), price * .002)
                    entry = price * (1 + self.slippage if long else 1 - self.slippage)
                    stop = entry - 2 * atr if long else entry + 2 * atr
                    target = entry + 3 * atr if long else entry - 3 * atr
                    qty = equity * risk_per_trade / abs(entry - stop)
                    trade = {"side":side,"entry":entry,"stop":stop,"target":target,"qty":qty,"timestamp":str(df.index[i])}
            curve.append(equity)
        pnls = [t["pnl"] for t in trades]; wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p < 0]
        peak = np.maximum.accumulate(curve); dd = min((value / top - 1) * 100 for value, top in zip(curve, peak))
        return BacktestResult(strategy, len(trades), round(100 * len(wins)/len(trades), 1) if trades else 0, round((equity/self.initial_capital-1)*100,2), round(dd,2), round(sum(wins)/abs(sum(losses)),2) if losses else 0, trades)
