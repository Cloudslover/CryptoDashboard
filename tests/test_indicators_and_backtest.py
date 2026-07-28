import numpy as np
import pandas as pd
from data.indicators import TechnicalIndicators
from data.backtester import Backtester


def candles(n=260):
    index = pd.date_range("2024-01-01", periods=n, freq="h")
    close = np.linspace(40_000, 50_000, n) + np.sin(np.arange(n) / 5) * 300
    return pd.DataFrame({"open":close-20,"high":close+100,"low":close-100,"close":close,"volume":np.linspace(10,20,n)}, index=index)


def test_indicators_do_not_mutate_input_and_supply_trade_columns():
    source = candles()
    result = TechnicalIndicators.add_all_indicators(source)
    assert "rsi" not in source
    assert {"ema_200", "rsi", "atr", "supertrend_bull"}.issubset(result.columns)
    assert result.index.equals(source.index)


def test_backtest_returns_a_result_for_valid_history():
    result = Backtester().run_strategy(candles())
    assert result.strategy == "trend_pullback"
    assert result.total_trades >= 0
    assert result.max_drawdown <= 0
