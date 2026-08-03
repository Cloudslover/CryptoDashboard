# brain/decision_maker.py — human approval queue with portfolio protection hooks
from datetime import datetime
from typing import List, Optional, Dict
import logging
import json

logger = logging.getLogger(__name__)


class DecisionManager:

    def __init__(self, db=None, portfolio_guardian=None, brain_memory=None):
        self.db = db
        self.portfolio_guardian = portfolio_guardian
        self.brain_memory = brain_memory

        self.pending   = []
        self.approved  = []
        self.rejected  = []
        self.open      = []
        self.history   = []
        self._counter  = 0

        # Load open from DB via portfolio if available
        self._load_state()
        logger.info("[OK] DecisionManager ready")

    def _load_state(self):
        if self.db:
            try:
                df = self.db.get_decision_history(limit=50)
                # Restore pending from DB that are still PENDING?
                # For simplicity we keep in-memory pending empty at startup
                pass
            except Exception:
                pass

    def submit(self, ai_decision, stability_info: Optional[Dict] = None) -> dict:
        """Queue a *new* actionable plan for human review; never executes an order.

        stability_info: optional dict from BrainMemory.should_suppress_flip
                        {suppressed: bool, reason: str, stabilized_action: dict}
                        If suppressed, we do NOT queue a new conflicting plan.
        """
        # If brain memory says suppress, don't queue opposing thesis
        if stability_info and stability_info.get("suppressed"):
            return {
                "status": "SUPPRESSED",
                "reason": stability_info.get("reason", "Suppressed by stability guard"),
                "kept_action": stability_info.get("kept_action"),
            }

        if ai_decision.action in {"STAND_ASIDE", "WEAK_LONG", "WEAK_SHORT"}:
            return {"status": "NOT_QUEUED", "reason": "No high-conviction actionable setup."}

        # Do not create a new approval item every refresh for the same market thesis.
        for queued in self.pending:
            if queued["action"] == ai_decision.action and abs(queued["entry_price"] - ai_decision.entry_price) / max(ai_decision.entry_price, 1) < .003:
                return queued

        # Portfolio risk check before queuing (even before approval)
        # We don't block queuing, but we tag it with risk warning
        risk_warning = None
        if self.portfolio_guardian:
            can_open, msg = self.portfolio_guardian.can_open_new_trade({
                "position_size": ai_decision.position_size,
                "leverage": ai_decision.leverage,
                "risk_reward": ai_decision.risk_reward,
                "entry_price": ai_decision.entry_price,
                "stop_loss": ai_decision.stop_loss,
            })
            if not can_open:
                # Still queue but with warning — final block at approval time
                risk_warning = msg

        self._counter += 1

        # Calculate margin / risk details for journaling
        entry = float(ai_decision.entry_price) if ai_decision.entry_price else 0
        pos_size = float(ai_decision.position_size) if ai_decision.position_size else 0
        leverage = int(ai_decision.leverage) if ai_decision.leverage else 1
        margin_usd = round(pos_size * entry / 100 * 1000, 2) if entry and pos_size else 0
        rr = float(ai_decision.risk_reward) if ai_decision.risk_reward else 0

        rec = {
            "id":            self._counter,
            "timestamp":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "action":        ai_decision.action,
            "confidence":    round(ai_decision.confidence, 1),
            "entry_price":   entry,
            "stop_loss":     ai_decision.stop_loss,
            "take_profit_1": ai_decision.take_profit_1,
            "take_profit_2": ai_decision.take_profit_2,
            "take_profit_3": ai_decision.take_profit_3,
            "position_size": pos_size,
            "leverage":      leverage,
            "margin_usd":    margin_usd,
            "risk_reward":   rr,
            "risk_percent":  pos_size,
            "time_horizon":  ai_decision.time_horizon,
            "reasoning":     ai_decision.reasoning,
            "invalidation":  ai_decision.invalidation,
            "trade_quality": getattr(ai_decision, "trade_quality", None),
            "status":        "PENDING_APPROVAL",
            "risk_warning":  risk_warning,
            "stability_note": stability_info.get("reason") if stability_info else None,
        }
        self.pending.append(rec)

        # Persist to signal_memory too via brain_memory already done upstream
        return rec

    def approve_trade(self, trade_id: int,
                      modify_sl=None, modify_tp=None,
                      modify_size=None, notes="") -> dict:
        t = self._find(trade_id)
        if not t:
            return {"error": f"Trade {trade_id} not found"}

        # Final portfolio protection check at approval time
        if self.portfolio_guardian:
            can_open, msg = self.portfolio_guardian.can_open_new_trade({
                "position_size": modify_size if modify_size else t["position_size"],
                "leverage": t["leverage"],
                "risk_reward": t["risk_reward"],
                "entry_price": t["entry_price"],
                "stop_loss": modify_sl if modify_sl else t["stop_loss"],
            })
            if not can_open:
                return {"error": f"Portfolio protection blocked approval: {msg}"}

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

        # Sync to portfolio guardian and brain memory
        if self.portfolio_guardian:
            self.portfolio_guardian.record_open(t)
        if self.brain_memory:
            self.brain_memory.record_approval(t)
        if self.db:
            try:
                self.db.update_decision(trade_id, "APPROVED", notes=notes)
                self.db.save_portfolio_trade(t, status="APPROVED")
            except Exception as exc:
                logger.warning(f"approve persist failed: {exc}")

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
        if self.db:
            try:
                self.db.update_decision(trade_id, "REJECTED", notes=reason)
            except Exception:
                pass
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

        if self.portfolio_guardian:
            self.portfolio_guardian.record_close(trade_id, exit_price, notes)
        if self.db:
            try:
                self.db.update_decision(trade_id, t["status"], exit_price, pnl, notes)
            except Exception:
                pass

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
    def _find(self, tid): return next((t for t in self.pending if t["id"]==tid), None)
