import pytest
from brain.decision_maker import DecisionManager


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
