"""Human-approved paper-plan lifecycle management.

The manager never talks to an exchange.  When a database is supplied, every state
transition is persisted so approved/open plans and their results survive restarts.
"""
from __future__ import annotations

from datetime import datetime
import logging
import math
import threading

logger = logging.getLogger(__name__)


class DecisionManager:
    def __init__(self, database=None):
        self.database = database
        self.pending = []
        self.approved = []
        self.rejected = []
        self.open = []
        self.history = []
        self._counter = 0
        self._lock = threading.RLock()
        self._restore()
        logger.info("[OK] DecisionManager ready (%d persisted paper plans)", self._counter)

    def _restore(self):
        """Restore paper plans from SQLite, if persistence is configured."""
        if self.database is None or not hasattr(self.database, "load_paper_trades"):
            return
        try:
            records = self.database.load_paper_trades()
        except Exception as exc:
            logger.warning("Could not restore paper plans: %s", exc)
            return
        for trade in records:
            try:
                self._counter = max(self._counter, int(trade["id"]))
            except (KeyError, TypeError, ValueError):
                continue
            status = trade.get("status")
            if status == "PENDING_APPROVAL":
                self.pending.append(trade)
            elif status == "APPROVED":
                self.approved.append(trade)
                self.open.append(trade)
            elif status == "REJECTED":
                self.rejected.append(trade)
            elif status in {"WIN", "LOSS"}:
                self.approved.append(trade)
                self.history.append(trade)

    def _persist_new(self, trade: dict) -> int:
        if self.database is None or not hasattr(self.database, "create_paper_trade"):
            return 0
        try:
            return int(self.database.create_paper_trade(trade) or 0)
        except Exception as exc:
            logger.exception("Could not persist new paper plan: %s", exc)
            return 0

    def _persist_update(self, trade: dict):
        if self.database is None or not hasattr(self.database, "update_paper_trade"):
            return
        try:
            self.database.update_paper_trade(trade)
        except Exception as exc:
            logger.exception("Could not persist paper plan #%s: %s", trade.get("id"), exc)

    @staticmethod
    def _same_thesis(trade: dict, ai_decision) -> bool:
        old_entry = trade.get("entry_price")
        new_entry = getattr(ai_decision, "entry_price", None)
        if old_entry is None or new_entry is None:
            return False
        try:
            close_entry = abs(float(old_entry) - float(new_entry)) / max(abs(float(new_entry)), 1) < .003
        except (TypeError, ValueError):
            return False
        return trade.get("action") == ai_decision.action and close_entry

    def submit(self, ai_decision) -> dict:
        """Queue a *new* actionable plan for human review; never execute an order."""
        if ai_decision.action in {"STAND_ASIDE", "WEAK_LONG", "WEAK_SHORT"}:
            return {"status": "NOT_QUEUED", "reason": "No high-conviction actionable setup."}
        if getattr(ai_decision, "entry_price", None) is None:
            return {"status": "NOT_QUEUED", "reason": "The plan has no valid entry reference."}

        with self._lock:
            # Do not create another approval item every refresh—or immediately after
            # approval—while the same market thesis is already pending/open.
            for queued in self.pending + self.open:
                if self._same_thesis(queued, ai_decision):
                    return queued.copy()

            rec = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "action": ai_decision.action,
                "confidence": round(ai_decision.confidence, 1),
                "entry_price": ai_decision.entry_price,
                "stop_loss": ai_decision.stop_loss,
                "take_profit_1": ai_decision.take_profit_1,
                "take_profit_2": ai_decision.take_profit_2,
                "take_profit_3": ai_decision.take_profit_3,
                "position_size": ai_decision.position_size,
                "leverage": ai_decision.leverage,
                "time_horizon": ai_decision.time_horizon,
                "risk_reward": ai_decision.risk_reward,
                "reasoning": ai_decision.reasoning,
                "invalidation": ai_decision.invalidation,
                "status": "PENDING_APPROVAL",
                "approved_at": None,
                "closed_at": None,
                "exit_price": None,
                "pnl_pct": None,
                "notes": "",
                "reject_reason": "",
            }
            persisted_id = self._persist_new(rec)
            if persisted_id:
                rec["id"] = persisted_id
                self._counter = max(self._counter, persisted_id)
            else:
                self._counter += 1
                rec["id"] = self._counter
            self.pending.append(rec)
            return rec.copy()

    def approve_trade(self, trade_id: int, modify_sl=None, modify_tp=None,
                      modify_size=None, notes="") -> dict:
        with self._lock:
            trade = self._find(trade_id)
            if not trade:
                return {"error": f"Trade {trade_id} not found"}
            trade["status"] = "APPROVED"
            trade["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            trade["notes"] = notes
            if modify_sl is not None:
                trade["stop_loss"] = modify_sl
            if modify_tp is not None:
                trade["take_profit_1"] = modify_tp
            if modify_size is not None:
                trade["position_size"] = modify_size
            self.pending.remove(trade)
            self.approved.append(trade)
            self.open.append(trade)
            self._persist_update(trade)
            return {"success": True, "trade": trade.copy()}

    def reject_trade(self, trade_id: int, reason="") -> dict:
        with self._lock:
            trade = self._find(trade_id)
            if not trade:
                return {"error": f"Trade {trade_id} not found"}
            trade["status"] = "REJECTED"
            trade["reject_reason"] = reason
            self.pending.remove(trade)
            self.rejected.append(trade)
            self._persist_update(trade)
            return {"success": True}

    @staticmethod
    def calculate_pnl_pct(trade: dict, price: float):
        """Return the directional BTC move in percent, before fees/funding."""
        try:
            entry = float(trade["entry_price"])
            price = float(price)
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(entry) or not math.isfinite(price) or entry <= 0 or price <= 0:
            return None
        if "LONG" in trade.get("action", ""):
            return (price - entry) / entry * 100
        if "SHORT" in trade.get("action", ""):
            return (entry - price) / entry * 100
        return None

    def close_trade(self, trade_id: int, exit_price: float, notes="") -> dict:
        with self._lock:
            trade = next((x for x in self.open if x["id"] == trade_id), None)
            if not trade:
                return {"error": "Trade not found"}
            pnl = self.calculate_pnl_pct(trade, exit_price)
            if pnl is None:
                return {"error": "A positive, finite exit price is required"}
            trade.update({
                "exit_price": float(exit_price),
                "pnl_pct": round(pnl, 2),
                "status": "WIN" if pnl > 0 else "LOSS",
                "closed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "notes": notes,
            })
            self.open.remove(trade)
            self.history.append(trade)
            self._persist_update(trade)
            return {"success": True, "pnl": pnl, "status": trade["status"]}

    def get_performance_stats(self) -> dict:
        with self._lock:
            history = [trade.copy() for trade in self.history]
        if not history:
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                    "avg_win": 0, "avg_loss": 0, "total_pnl": 0, "profit_factor": 0}
        wins = [t for t in history if t["status"] == "WIN"]
        losses = [t for t in history if t["status"] == "LOSS"]
        total = len(history)
        wr = len(wins) / total * 100
        avg_w = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
        avg_l = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
        total_pnl = sum(t["pnl_pct"] for t in history)
        gross_profit = sum(t["pnl_pct"] for t in wins)
        gross_loss = abs(sum(t["pnl_pct"] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss else 0
        return {
            "total": total, "wins": len(wins), "losses": len(losses),
            "win_rate": round(wr, 1), "avg_win": round(avg_w, 2),
            "avg_loss": round(avg_l, 2), "total_pnl": round(total_pnl, 2),
            "profit_factor": round(profit_factor, 2),
        }

    def get_pending_summary(self):
        with self._lock:
            return [trade.copy() for trade in self.pending]

    def get_open_trades_summary(self, current_price=None):
        with self._lock:
            records = [trade.copy() for trade in self.open]
        for trade in records:
            trade["current_price"] = current_price
            trade["unrealized_pnl_pct"] = self.calculate_pnl_pct(trade, current_price)
        return records

    def get_trade_history(self):
        with self._lock:
            return [trade.copy() for trade in reversed(self.history)]

    def _find(self, trade_id):
        return next((trade for trade in self.pending if trade["id"] == trade_id), None)
