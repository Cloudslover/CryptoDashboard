"""Offline tests for the Polymarket prediction-market intelligence module.

These never touch the network: they exercise the parsing, shift/baseline and
summary logic that the module runs on live data.
"""
import types
import pandas as pd
import pytest

import brain.polymarket as pm


def raw_market(slug="bitcoin-150k", yes="0.62", no="0.38", closed=False):
    return {
        "slug": slug,
        "question": "Will Bitcoin reach $150,000 before 2027?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": f'["{yes}", "{no}"]',
        "lastTradePrice": float(yes),
        "volume": "12345.0",
        "liquidity": "500.0",
        "active": True,
        "closed": closed,
    }


def test_parse_market_handles_stringified_arrays():
    monitor = pm.PolymarketMonitor()
    m = monitor._parse_market(raw_market(), "CRYPTO")
    assert m is not None
    assert m.slug == "bitcoin-150k"
    assert m.category == "CRYPTO"
    assert m.yes_price == 62.0          # 0.62 -> 62%
    assert m.last_trade_price == 0.62


def test_parse_market_skips_closed_or_unpriced():
    monitor = pm.PolymarketMonitor()
    assert monitor._parse_market(raw_market(closed=True), "CRYPTO") is None
    assert monitor._parse_market(raw_market(yes="0", no="1"), "CRYPTO") is None
    assert monitor._parse_market({}, "CRYPTO") is None


def test_build_summary_computes_shifts_and_bias():
    monitor = pm.PolymarketMonitor()
    m1 = monitor._parse_market(raw_market(slug="a", yes="0.70"), "CRYPTO")
    m2 = monitor._parse_market(raw_market(slug="b", yes="0.40"), "MACRO")
    baseline = {"a": 60.0, "b": 50.0}   # yes_price pct values
    summary = monitor.build_summary([m1, m2], baseline, btc_change_pct=1.5)
    by = {x["slug"]: x for x in summary["markets"]}
    assert by["a"]["change_1d"] == 10.0    # 70 - 60
    assert by["b"]["change_1d"] == -10.0
    assert summary["crypto_prob_shift"] == 10.0
    assert summary["macro_prob_shift"] == -10.0
    # Net: crypto(10)*1.0 + macro(-10)*0.6 = +4 -> BULLISH
    assert summary["overall_bias"] == "BULLISH"
    assert summary["biggest_shifts"][0]["slug"] == "a"


def test_build_summary_reports_conflict_correlation():
    monitor = pm.PolymarketMonitor()
    m1 = monitor._parse_market(raw_market(slug="a", yes="0.70"), "CRYPTO")
    summary = monitor.build_summary([m1], {"a": 60.0}, btc_change_pct=-0.5)
    assert "Conflict" in summary["correlation_note"]
    summary2 = monitor.build_summary([m1], {}, btc_change_pct=1.0)
    assert "baseline" in summary2["correlation_note"]


def test_get_summary_retains_last_snapshot_on_outage(monkeypatch):
    class FakeDB:
        def load_polymarket_history(self):
            return pd.DataFrame()
        def save_polymarket(self, markets):
            self.saved = markets

    monitor = pm.PolymarketMonitor()

    def boom(url, params=None, timeout=None, headers=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr(pm, "get_json", boom)
    db = FakeDB()
    summary = monitor.get_summary(db)
    assert summary["markets"] == []

    # Now seed a known summary and force an outage path: an empty fetch while a
    # cached summary exists should return the cached value.
    monitor.cache["summary"] = {"markets": [{"slug": "cached"}], "overall_bias": "NEUTRAL"}
    monitor.cache_time["summary"] = 0  # expire TTL
    again = monitor.get_summary(db)
    assert again["markets"] == [{"slug": "cached"}]
