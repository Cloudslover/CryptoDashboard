"""Tests for the Trade Quality scorer and CVD order-flow computation.

These are offline: they feed deterministic synthetic data through the pure
scoring/parsing logic rather than hitting the network.
"""
import types

import numpy as np
import pandas as pd

from brain.trade_quality import TradeQualityScorer
from data.indicators import TechnicalIndicators


def build_df(n=260):
    idx = pd.date_range("2026-01-01", periods=n, freq="h")
    close = np.linspace(100_000, 101_000, n) + np.sin(np.arange(n) / 5) * 200
    return pd.DataFrame({
        "open": close - 20, "high": close + 100, "low": close - 100,
        "close": close, "volume": np.linspace(10, 20, n),
    }, index=idx)


def make_decision(action="LONG", entry=100_000, stop=98_000, tp=104_000, rr=2.0):
    return types.SimpleNamespace(
        action=action, entry_price=entry, stop_loss=stop,
        take_profit_1=tp, risk_reward=rr,
    )


def test_scorer_returns_all_factors_and_total():
    df = TechnicalIndicators.add_all_indicators(build_df())
    scorer = TradeQualityScorer()
    res = scorer.score(
        make_decision(), df, [],
        {"consensus": {"weighted_bias": 2}},
        {"macro_bias": "BULLISH"},
        {"overall_sentiment": 0.1, "critical_news": []},
        {"current": 0.01}, {"change_24h": 3.0},
        {"buy_pressure": 65, "total_volume": 1000, "divergence": False},
        {"overall_bias": "BULLISH"},
    )
    assert 0 <= res["total"] <= 100
    assert len(res["factors"]) == 10
    names = {f["name"] for f in res["factors"]}
    assert {"Market Structure", "Trend", "Volume", "Funding", "Open Interest",
            "Whale Activity", "News Risk", "Macro Alignment", "Sentiment",
            "Risk/Reward"}.issubset(names)
    assert all(f["reason"] for f in res["factors"])  # explainable


def test_scorer_total_reflects_bad_conditions():
    df = TechnicalIndicators.add_all_indicators(build_df())
    scorer = TradeQualityScorer()
    good = scorer.score(
        make_decision(rr=3.0), df, [], {"consensus": {"weighted_bias": 3}},
        {"macro_bias": "STRONGLY BULLISH"},
        {"overall_sentiment": 0.5, "critical_news": []},
        {"current": 0.01}, {"change_24h": 2.0},
        {"buy_pressure": 70, "total_volume": 1000, "divergence": False},
        {"overall_bias": "BULLISH"})
    bad = scorer.score(
        make_decision(rr=0.8), df, [], {"consensus": {"weighted_bias": -3}},
        {"macro_bias": "STRONGLY BEARISH"},
        {"overall_sentiment": -0.5, "critical_news": []},
        {"current": 0.05}, {"change_24h": 15.0},
        {"buy_pressure": 20, "total_volume": 1000, "divergence": True},
        {"overall_bias": "BEARISH"})
    assert good["total"] > bad["total"]


def test_cvd_pressure_and_divergence(monkeypatch):
    from data.fetcher import BTCDataFetcher
    f = BTCDataFetcher()
    # Rising price but majority sell-taker flow -> negative delta + divergence.
    trades = pd.DataFrame({
        "time": pd.date_range("2026-01-01", periods=20, freq="s"),
        "price": np.linspace(100_000, 100_200, 20),   # price up
        "qty": np.full(20, 1.0),
        # is_buyer_maker True => seller was the taker (sell flow).
        "is_buyer_maker": [True] * 15 + [False] * 5,
    })
    monkeypatch.setattr(f, "get_agg_trades", lambda limit=None: trades)
    res = f.get_cvd()
    assert res["sell_volume"] > res["buy_volume"]
    assert res["net_delta"] < 0
    assert res["buy_pressure"] < 50
    assert res["divergence"] is True
    assert res["n_trades"] == 20


def test_cvd_degrades_when_no_trades(monkeypatch):
    from data.fetcher import BTCDataFetcher
    f = BTCDataFetcher()
    empty = pd.DataFrame(columns=["time", "price", "qty", "is_buyer_maker"])
    monkeypatch.setattr(f, "get_agg_trades", lambda limit=None: empty)
    res = f.get_cvd()
    assert res["status"] == "Unknown"
    assert res["n_trades"] == 0
