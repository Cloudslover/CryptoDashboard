# brain/decision_maker.py
from datetime import datetime
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


class DecisionManager:

    def __init__(self):
        self.pending   = []
        self.approved  = []
        self.rejected  = []
        self.open      = []
        self.history   = []
        self._counter  = 0
        logger.info("[OK] DecisionManager ready")

    def submit(self, ai_decision) -> dict:
        self._counter += 1
        rec = {
            "id":            self._counter,
            "timestamp":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action":        ai_decision.action,
            "confidence":    round(ai_decision.confidence, 1),
            "entry_price":   ai_decision.entry_price,
            "stop_loss":     ai_decision.stop_loss,
            "take_profit_1": ai_decision.take_profit_1,
            "take_profit_2": ai_decision.take_profit_2,
            "take_profit_3": ai_decision.take_profit_3,
            "position_size": ai_decision.position_size,
            "leverage":      ai_decision.leverage,
            "time_horizon":  ai_decision.time_horizon,
            "risk_reward":   ai_decision.risk_reward,
            "reasoning":     ai_decision.reasoning,
            "invalidation":  ai_decision.invalidation,
            "status":        "PENDING_APPROVAL",
        }
        self.pending.append(rec)
        return rec

    def approve_trade(self, trade_id: int,
                      modify_sl=None, modify_tp=None,
                      modify_size=None, notes="") -> dict:
        t = self._find(trade_id)
        if not t:
            return {"error": f"Trade {trade_id} not found"}
        t["status"]      = "APPROVED"
        t["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        t["notes"]       = notes
        if modify_sl:   t["stop_loss"]      = modify_sl
        if modify_tp:   t["take_profit_1"]  = modify_tp
        if modify_size: t["position_size"]  = modify_size
        try:
            self.pending.remove(t)
        except ValueError:
            pass
        self.approved.append(t)
        self.open.append(t)
        return {"success": True, "trade": t}

    def reject_trade(self, trade_id: int, reason="") -> dict:
        t = self._find(trade_id)
        if not t:
            return {"error": f"Trade {trade_id} not found"}
        t["status"]       = "REJECTED"
        t["reject_reason"]= reason
        try:
            self.pending.remove(t)
        except ValueError:
            pass
        self.rejected.append(t)
        return {"success": True}

    def close_trade(self, trade_id: int, exit_price: float, notes="") -> dict:
        t = next((x for x in self.open if x["id"]==trade_id), None)
        if not t:
            return {"error": "Trade not found"}
        entry  = t["entry_price"]
        is_lng = "LONG" in t["action"]
        pnl    = (exit_price-entry)/entry*100 if is_lng else (entry-exit_price)/entry*100
        t.update({
            "exit_price": exit_price,
            "pnl_pct":    round(pnl,2),
            "status":     "WIN" if pnl>0 else "LOSS",
            "closed_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "notes":      notes,
        })
        try:
            self.open.remove(t)
        except ValueError:
            pass
        self.history.append(t)
        return {"success": True, "pnl": pnl, "status": t["status"]}

    def get_performance_stats(self) -> dict:
        if not self.history:
            return {"total":0,"wins":0,"losses":0,"win_rate":0,
                    "avg_win":0,"avg_loss":0,"total_pnl":0,"profit_factor":0}
        wins   = [t for t in self.history if t["status"]=="WIN"]
        losses = [t for t in self.history if t["status"]=="LOSS"]
        total  = len(self.history)
        wr     = len(wins)/total*100 if total>0 else 0
        avg_w  = sum(t["pnl_pct"] for t in wins)/len(wins) if wins else 0
        avg_l  = sum(t["pnl_pct"] for t in losses)/len(losses) if losses else 0
        t_pnl  = sum(t["pnl_pct"] for t in self.history)
        pf     = abs(avg_w*len(wins))/abs(avg_l*len(losses)) if losses and avg_l!=0 else 0
        return {
            "total": total, "wins": len(wins), "losses": len(losses),
            "win_rate": round(wr,1), "avg_win": round(avg_w,2),
            "avg_loss": round(avg_l,2), "total_pnl": round(t_pnl,2),
            "profit_factor": round(pf,2),
        }

    def get_pending_summary(self): return self.pending
    def get_open_trades_summary(self): return self.open
    def _find(self, tid): return next((t for t in self.pending if t["id"]==tid), None
