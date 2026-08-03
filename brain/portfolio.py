"""Portfolio Guardian — protect fund, enforce risk, track every approved trade.

Your portfolio is more important than a single signal. This module:
- Tracks open positions with full details (leverage, margin, RR, entry, SL, TP)
- Enforces max risk per trade & aggregate risk
- Blocks new positions if risk limits exceeded
- Tracks daily PnL, max drawdown kill-switch
- Provides fund protection status for dashboard

Inspired by blockchain explorers' auditability: every satoshi of risk is logged.
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import json

logger = logging.getLogger(__name__)


class PortfolioGuardian:
    def __init__(self, db=None,
                 max_risk_per_trade: float = 1.0,
                 max_total_risk: float = 3.0,
                 max_open_trades: int = 2,
                 daily_loss_limit_pct: float = 3.0,
                 max_leverage: int = 10):
        self.db = db
        self.max_risk_per_trade = max_risk_per_trade
        self.max_total_risk = max_total_risk
        self.max_open_trades = max_open_trades
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.max_leverage = max_leverage

        self.open_trades: List[Dict] = []
        self.closed_today: List[Dict] = []
        self.daily_pnl = 0.0

        self._load_open_trades()
        logger.info(f"[OK] PortfolioGuardian ready (per-trade {max_risk_per_trade}% total {max_total_risk}% max_open {max_open_trades} daily_stop -{daily_loss_limit_pct}%)")

    def _load_open_trades(self):
        if not self.db:
            return
        try:
            df = self.db.get_open_portfolio_trades()
            if df is not None and not df.empty:
                self.open_trades = df.to_dict(orient="records")
                logger.info(f"PortfolioGuardian loaded {len(self.open_trades)} open trades")
        except Exception as exc:
            logger.warning(f"PortfolioGuardian load failed: {exc}")

    # ── Risk checks before approving new trade ─────────────────────────
    def can_open_new_trade(self, proposed: Dict) -> Tuple[bool, str]:
        """Check if new trade would violate risk guardrails."""

        # 1. Max open trades
        if len(self.open_trades) >= self.max_open_trades:
            return False, f"Max open trades reached ({len(self.open_trades)}/{self.max_open_trades}). Close one before new."

        # 2. Risk per trade
        risk = float(proposed.get("position_size", proposed.get("risk_percent", 1.0)))
        if risk > self.max_risk_per_trade * 1.5:  # allow 50% buffer but warn
            return False, f"Risk per trade {risk}% exceeds max {self.max_risk_per_trade}% (1.5x buffer = {self.max_risk_per_trade*1.5}%). Reduce size."

        # 3. Total risk
        current_total = sum(float(t.get("position_size", t.get("risk_percent", 0))) for t in self.open_trades)
        if current_total + risk > self.max_total_risk:
            return False, f"Total risk {current_total + risk:.2f}% would exceed max {self.max_total_risk}%. Current open risk {current_total:.2f}% + proposed {risk:.2f}%."

        # 4. Leverage check
        lev = int(proposed.get("leverage", 1))
        if lev > self.max_leverage:
            return False, f"Leverage {lev}x exceeds max {self.max_leverage}x. Too risky."

        # 5. Daily loss kill-switch
        if self.daily_pnl <= -self.daily_loss_limit_pct:
            return False, f"Daily loss limit hit: {self.daily_pnl:.2f}% ≤ -{self.daily_loss_limit_pct}%. Trading paused today to protect fund. 🛡️"

        # 6. RR check
        rr = float(proposed.get("risk_reward", 0))
        if rr < 1.5 and rr != 0:
            return False, f"Risk-reward {rr:.2f} < 1.5 minimum. Reward too small vs risk."

        # 7. Must have stop loss
        sl = float(proposed.get("stop_loss", 0))
        entry = float(proposed.get("entry_price", 0))
        if not sl or abs(entry - sl) / max(entry, 1) < 0.001:
            return False, "Stop-loss missing or too tight (<0.1%). Every trade must have protective SL."

        return True, f"✅ Risk OK: total {current_total + risk:.2f}%/{self.max_total_risk}%, open {len(self.open_trades)}/{self.max_open_trades}, lev {lev}x, RR {rr:.2f}"

    def record_open(self, trade: Dict):
        """Record approved trade as open."""
        try:
            trade["opened_at"] = datetime.now().isoformat()
            trade["status"] = "OPEN"
            self.open_trades.append(trade)

            if self.db:
                try:
                    self.db.save_portfolio_trade(trade, status="OPEN")
                except Exception as exc:
                    logger.warning(f"record_open DB failed: {exc}")

            logger.info(f"PortfolioGuardian: OPEN {trade.get('action')} @ {trade.get('entry_price')} ID {trade.get('id')} lev {trade.get('leverage')}x RR {trade.get('risk_reward')}")
        except Exception as exc:
            logger.exception(f"record_open failed: {exc}")

    def record_close(self, trade_id: int, exit_price: float, notes: str = "") -> Dict:
        """Close trade, calculate PnL, update daily."""
        try:
            t = next((x for x in self.open_trades if x.get("id") == trade_id), None)
            if not t:
                return {"error": f"Trade {trade_id} not found in open"}

            entry = float(t.get("entry_price", 0))
            is_long = "LONG" in t.get("action", "")
            pnl = (exit_price - entry) / entry * 100 if is_long else (entry - exit_price) / entry * 100
            # Apply leverage? For % PnL on margin, multiply, but we keep raw for now and also leveraged
            lev = int(t.get("leverage", 1))
            pnl_lev = pnl * lev  # simplified exposure

            t.update({
                "exit_price": exit_price,
                "pnl_pct": round(pnl, 2),
                "pnl_lev": round(pnl_lev, 2),
                "status": "WIN" if pnl > 0 else "LOSS",
                "closed_at": datetime.now().isoformat(),
                "notes": notes,
            })

            # Update daily PnL
            self.daily_pnl += pnl
            self.closed_today.append(t)

            try:
                self.open_trades.remove(t)
            except ValueError:
                pass

            if self.db:
                try:
                    self.db.update_portfolio_trade(trade_id, status=t["status"], exit_price=exit_price, pnl_pct=pnl, notes=notes)
                except Exception as exc:
                    logger.warning(f"record_close DB failed: {exc}")

            logger.info(f"PortfolioGuardian: CLOSE ID {trade_id} PnL {pnl:.2f}% ({pnl_lev:.2f}% lev) daily {self.daily_pnl:.2f}%")
            return {"success": True, "pnl": pnl, "pnl_lev": pnl_lev, "status": t["status"], "daily_pnl": self.daily_pnl}
        except Exception as exc:
            logger.exception(f"record_close failed: {exc}")
            return {"error": str(exc)}

    def get_exposure(self) -> Dict:
        total_risk = sum(float(t.get("position_size", t.get("risk_percent", 0))) for t in self.open_trades)
        long_exposure = sum(float(t.get("position_size", 0)) for t in self.open_trades if "LONG" in t.get("action", ""))
        short_exposure = sum(float(t.get("position_size", 0)) for t in self.open_trades if "SHORT" in t.get("action", ""))
        return {
            "open_count": len(self.open_trades),
            "total_risk": round(total_risk, 2),
            "long_risk": round(long_exposure, 2),
            "short_risk": round(short_exposure, 2),
            "net_direction": "LONG" if long_exposure > short_exposure else "SHORT" if short_exposure > long_exposure else "NEUTRAL",
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_loss_limit": self.daily_loss_limit_pct,
            "can_trade": self.daily_pnl > -self.daily_loss_limit_pct and len(self.open_trades) < self.max_open_trades,
        }

    def get_open_summary(self) -> List[Dict]:
        return self.open_trades

    def reset_daily_if_needed(self):
        """Reset daily PnL at start of new day."""
        try:
            # Check if last closed trade was previous day
            if not self.closed_today:
                return
            # For simplicity, reset if daily PnL tracking date changed — would need date storage; skip auto for now
            pass
        except Exception:
            pass

    def get_protection_status(self) -> Dict:
        exposure = self.get_exposure()
        status = "SAFE"
        reasons = []

        if exposure["total_risk"] >= self.max_total_risk * 0.9:
            status = "WARNING"
            reasons.append(f"High total risk {exposure['total_risk']}% near limit {self.max_total_risk}%")
        if exposure["open_count"] >= self.max_open_trades:
            status = "BLOCKED"
            reasons.append(f"Max open trades {exposure['open_count']}/{self.max_open_trades}")
        if exposure["daily_pnl"] <= -self.daily_loss_limit_pct * 0.7:
            status = "WARNING" if status != "BLOCKED" else status
            reasons.append(f"Daily PnL {exposure['daily_pnl']}% approaching stop -{self.daily_loss_limit_pct}%")
        if exposure["daily_pnl"] <= -self.daily_loss_limit_pct:
            status = "KILL_SWITCH"
            reasons.append(f"Daily loss kill-switch: {exposure['daily_pnl']}% ≤ -{self.daily_loss_limit_pct}% — trading halted")

        if not reasons:
            reasons.append(f"Portfolio healthy: {exposure['open_count']} open, {exposure['total_risk']}% risk, daily {exposure['daily_pnl']:+.2f}%")

        return {
            "status": status,
            "reasons": reasons,
            "exposure": exposure,
        }
