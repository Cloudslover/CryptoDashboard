import pandas as pd
import pytest

from brain.ai_engine import AIBrain
from brain.decision_maker import DecisionManager
from data.database import Database


class DummyDecision:
    def __init__(self):
        self.action = "LONG BTC"
        self.confidence = 75.2
        self.entry_price = 30000.0
        self.stop_loss = 29000.0
        self.take_profit_1 = 32000.0
        self.take_profit_2 = 33000.0
        self.take_profit_3 = 34000.0
        self.position_size = 0.01
        self.leverage = 1
        self.time_horizon = "1D"
        self.risk_reward = 2.0
        self.reasoning = "Test trade"
        self.invalidation = "Stop loss hit"


def test_submit_and_approve_close():
    dm = DecisionManager()
    dec = DummyDecision()
    rec = dm.submit(dec)
    assert rec["status"] == "PENDING_APPROVAL"
    tid = rec["id"]

    # Approve trade
    res = dm.approve_trade(tid)
    assert res["success"] is True
    assert any(t["id"] == tid for t in dm.approved)
    assert any(t["id"] == tid for t in dm.open)

    # Close trade with profit
    close = dm.close_trade(tid, exit_price=33000.0, notes="target hit")
    assert close["success"] is True
    assert close["pnl"] is not None
    assert any(t["id"] == tid for t in dm.history)

    stats = dm.get_performance_stats()
    assert stats["total"] == 1
    assert stats["wins"] + stats["losses"] == 1


def test_approved_plan_and_result_survive_restart(tmp_path):
    database = Database(tmp_path / "paper.db")
    manager = DecisionManager(database)
    record = manager.submit(DummyDecision())
    trade_id = record["id"]
    assert manager.approve_trade(trade_id)["success"] is True

    restarted = DecisionManager(database)
    open_trades = restarted.get_open_trades_summary(current_price=31_500)
    assert [trade["id"] for trade in open_trades] == [trade_id]
    assert open_trades[0]["unrealized_pnl_pct"] == pytest.approx(5.0)
    assert restarted.close_trade(trade_id, 33_000)["status"] == "WIN"

    restarted_again = DecisionManager(database)
    assert restarted_again.get_open_trades_summary() == []
    assert restarted_again.get_trade_history()[0]["status"] == "WIN"
    assert restarted_again.get_performance_stats()["wins"] == 1


def test_stand_aside_has_no_trade_levels_and_zero_vix_is_not_bullish():
    brain = AIBrain()
    macro = {
        "macro_bias": "NEUTRAL",
        "fed": {"signal": "UNAVAILABLE"},
        "stocks": {"indices": {"VIX": {"price": 0, "change": 0, "available": False}}},
    }
    macro_score, reasons = brain._macro(macro)
    assert macro_score == 0
    assert any("unavailable" in reason for reason in reasons)

    decision = brain.make_decision(
        {"price": 30_000, "funding": {}}, [], {"consensus": {}},
        {"overall_sentiment": 0}, macro, {"score": 5}, pd.DataFrame(),
    )
    assert decision.action == "STAND_ASIDE"
    assert decision.entry_price is None
    assert decision.stop_loss is None
    assert decision.take_profit_1 is None
    assert decision.take_profit_2 is None
    assert decision.take_profit_3 is None
    assert decision.position_size == 0
    assert decision.leverage == 0
