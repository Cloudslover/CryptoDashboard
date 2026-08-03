"""Brain Memory — signal hysteresis, flip protection, and persistent trade journal.

This module solves the user's core problem:
  @3:00pm LONG @67000, @3:05pm SHORT @67100 — contradictory whipsaw.

It records EVERY signal/decision with full context (leverage, RR, margin, etc.)
and enforces:

* Time cooldown between opposite signals
* Price-move threshold to allow flip
* Confidence threshold to allow flip
* Portfolio protection: never suggest opposite while open position is active unless invalidation
* Deduplication: same thesis within 0.3% price does not create new approval item

This is inspired by how mempool.space handles state — we keep a mempool of pending
signals and a chain of confirmed (approved) trades.

All records are persisted via Database so restarts retain memory.
"""

from __future__ import annotations
import json
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, List

logger = logging.getLogger(__name__)

# Default guardrails — can be overridden via .env / config.py
DEFAULT_COOLDOWN_MINUTES = 30
DEFAULT_FLIP_PRICE_PCT = 0.8         # price must move >=0.8% against last signal to allow flip
DEFAULT_MIN_CONF_TO_FLIP = 75.0      # need 75%+ confidence to flip early
DEFAULT_SAME_THESIS_PCT = 0.3        # <0.3% price diff = same thesis, don't re-queue
DEFAULT_MAX_FLIP_PER_HOUR = 2        # sanity: no more than 2 flips per hour


def _action_direction(action: str) -> int:
    if not action:
        return 0
    a = action.upper()
    if "LONG" in a:
        return 1
    if "SHORT" in a:
        return -1
    return 0


def _opposite(dir1: int, dir2: int) -> bool:
    return dir1 != 0 and dir2 != 0 and dir1 == -dir2


class BrainMemory:
    """Persistent memory that remembers every setup you approve and protects portfolio."""

    def __init__(self, db=None,
                 cooldown_minutes: int = DEFAULT_COOLDOWN_MINUTES,
                 flip_price_pct: float = DEFAULT_FLIP_PRICE_PCT,
                 min_conf_to_flip: float = DEFAULT_MIN_CONF_TO_FLIP,
                 same_thesis_pct: float = DEFAULT_SAME_THESIS_PCT):
        self.db = db
        self.cooldown_minutes = cooldown_minutes
        self.flip_price_pct = flip_price_pct
        self.min_conf_to_flip = min_conf_to_flip
        self.same_thesis_pct = same_thesis_pct

        # In-memory cache of recent signals (fast path)
        self.recent_signals: List[Dict] = []  # newest first
        self.last_emitted: Optional[Dict] = None
        self._last_flip_time: Optional[datetime] = None
        self.flip_count_hour: int = 0

        # Load from DB if available
        self._load_memory()
        logger.info(f"[OK] BrainMemory ready (cooldown={cooldown_minutes}m flip_thresh={flip_price_pct}% min_conf={min_conf_to_flip}%)")

    # ── Persistence helpers ───────────────────────────────────────────
    def _load_memory(self):
        if not self.db:
            return
        try:
            df = self.db.get_signal_memory(limit=100)
            if df is not None and not df.empty:
                # pandas DataFrame to list of dicts
                records = df.to_dict(orient="records")
                # Newest first according to timestamp
                records.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                self.recent_signals = records[:50]
                if records:
                    self.last_emitted = records[0]
                logger.info(f"[OK] BrainMemory loaded {len(self.recent_signals)} signals from DB")
        except Exception as exc:
            logger.warning(f"BrainMemory load failed: {exc}")

    def _decision_hash(self, action: str, entry_price: float) -> str:
        # Stable hash for dedup: action bucketed + price bucketed to 0.3%
        price_bucket = round(entry_price * (100 / self.same_thesis_pct)) if entry_price else 0
        raw = f"{action.upper()}::{price_bucket}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ── Record ─────────────────────────────────────────────────────────
    def record_signal(self, decision) -> Dict:
        """Record every AI decision with full details for learning & protection."""
        try:
            if hasattr(decision, "__dict__"):
                # AIDecision dataclass or similar
                action = getattr(decision, "action", "UNKNOWN")
                confidence = float(getattr(decision, "confidence", 0))
                entry = float(getattr(decision, "entry_price", 0))
                sl = float(getattr(decision, "stop_loss", 0))
                tp1 = float(getattr(decision, "take_profit_1", 0))
                tp2 = float(getattr(decision, "take_profit_2", 0))
                tp3 = float(getattr(decision, "take_profit_3", 0))
                pos_size = float(getattr(decision, "position_size", 0))
                leverage = int(getattr(decision, "leverage", 1))
                rr = float(getattr(decision, "risk_reward", 0))
                reasoning = getattr(decision, "reasoning", [])
                invalidation = getattr(decision, "invalidation", "")
                horizon = getattr(decision, "time_horizon", "")
                tq = getattr(decision, "trade_quality", None)
            else:
                # dict
                d = decision if isinstance(decision, dict) else {}
                action = d.get("action", "UNKNOWN")
                confidence = float(d.get("confidence", 0))
                entry = float(d.get("entry_price", 0))
                sl = float(d.get("stop_loss", 0))
                tp1 = float(d.get("take_profit_1", 0))
                tp2 = float(d.get("take_profit_2", 0))
                tp3 = float(d.get("take_profit_3", 0))
                pos_size = float(d.get("position_size", 0))
                leverage = int(d.get("leverage", 1))
                rr = float(d.get("risk_reward", 0))
                reasoning = d.get("reasoning", [])
                invalidation = d.get("invalidation", "")
                horizon = d.get("time_horizon", "")
                tq = d.get("trade_quality")
                if isinstance(tq, str):
                    try:
                        tq = json.loads(tq)
                    except Exception:
                        tq = None

            ts = datetime.now()
            decision_hash = self._decision_hash(action, entry)
            margin_usd = round(pos_size * entry / 100.0 * 1000, 2) if entry and pos_size else 0  # approximate
            # risk % is position_size in this codebase (treated as %)
            # Calculate risk amount: |entry - sl|/entry * leverage?
            risk_pct = pos_size
            risk_amount = round(abs(entry - sl) / entry * 100 * risk_pct, 2) if entry and sl else 0

            record = {
                "timestamp": ts.isoformat(),
                "action": action,
                "confidence": confidence,
                "entry_price": entry,
                "stop_loss": sl,
                "take_profit_1": tp1,
                "take_profit_2": tp2,
                "take_profit_3": tp3,
                "position_size": pos_size,
                "leverage": leverage,
                "margin_usd": margin_usd,
                "risk_reward": rr,
                "risk_percent": risk_pct,
                "risk_amount_pct": risk_amount,
                "time_horizon": horizon,
                "reasoning": json.dumps(reasoning) if isinstance(reasoning, (list, dict)) else str(reasoning),
                "invalidation": invalidation,
                "trade_quality": json.dumps(tq) if tq else None,
                "decision_hash": decision_hash,
                "status": "PROPOSED",
            }

            # In-memory
            self.recent_signals.insert(0, record)
            self.recent_signals = self.recent_signals[:100]

            # Persist
            if self.db:
                try:
                    self.db.save_signal_memory(record)
                except Exception as exc:
                    logger.warning(f"save_signal_memory failed: {exc}")

            logger.info(f"BrainMemory: recorded {action} @ {entry:.0f} conf {confidence:.0f}% RR {rr} lev {leverage}x")
            return record
        except Exception as exc:
            logger.exception(f"record_signal failed: {exc}")
            return {}

    def record_approval(self, approved_trade: Dict):
        """Called when user approves – upgrades memory status and logs full portfolio details."""
        try:
            tid = approved_trade.get("id")
            # Update in-memory
            for rec in self.recent_signals:
                if abs(rec.get("entry_price", 0) - approved_trade.get("entry_price", 0)) / max(approved_trade.get("entry_price", 1), 1) < 0.005 \
                   and rec.get("action") == approved_trade.get("action"):
                    rec["status"] = "APPROVED"
                    rec["approved_at"] = datetime.now().isoformat()
                    break

            if self.db:
                try:
                    self.db.save_portfolio_trade(approved_trade)
                    # also update signal_memory status
                    if "entry_price" in approved_trade:
                        self.db.update_signal_memory_status(
                            entry_price=approved_trade["entry_price"],
                            action=approved_trade["action"],
                            status="APPROVED"
                        )
                except Exception as exc:
                    logger.warning(f"record_approval persist failed: {exc}")
        except Exception as exc:
            logger.warning(f"record_approval failed: {exc}")

    # ── Signal Stability / Flip Protection ─────────────────────────────
    def should_suppress_flip(self, new_decision, open_trades: List[Dict] = None) -> Tuple[bool, str, Optional[Dict]]:
        """Core logic that prevents LONG @ 67000 → SHORT @ 67100 whipsaw.

        Returns: (suppress: bool, reason: str, stabilized_action: dict|None)
        If suppress=True, the new decision should NOT be queued; keep previous bias.
        """
        if open_trades is None:
            open_trades = []

        # Extract fields
        if hasattr(new_decision, "action"):
            new_action = getattr(new_decision, "action", "STAND_ASIDE")
            new_conf = float(getattr(new_decision, "confidence", 0))
            new_price = float(getattr(new_decision, "entry_price", 0))
            new_rr = float(getattr(new_decision, "risk_reward", 0))
        else:
            d = new_decision if isinstance(new_decision, dict) else {}
            new_action = d.get("action", "STAND_ASIDE")
            new_conf = float(d.get("confidence", 0))
            new_price = float(d.get("entry_price", 0))
            new_rr = float(d.get("risk_reward", 0))

        new_dir = _action_direction(new_action)

        # No direction = always allow (STAND_ASIDE)
        if new_dir == 0:
            return False, "STAND_ASIDE allowed", None

        # 1. Check open positions — portfolio protection first
        for ot in open_trades:
            ot_dir = _action_direction(ot.get("action", ""))
            if _opposite(new_dir, ot_dir):
                # Is the open trade still valid? Check if price hit SL/invalidation zone
                # For now, we block flips while position open unless very high confidence + strong move
                ot_entry = float(ot.get("entry_price", 0))
                if ot_entry > 0:
                    price_move_pct = abs(new_price - ot_entry) / ot_entry * 100 if new_price else 0
                    # If price moved against open trade beyond 1.5% AND new confidence > 80, allow flip after warning
                    if price_move_pct >= 1.5 and new_conf >= 85:
                        return False, f"Portfolio protection override: open {ot.get('action')} @ {ot_entry:.0f} moved {price_move_pct:.2f}% against, high conf {new_conf:.0f}% allows flip", None
                    else:
                        # Block flip
                        return True, f"🛡️ PORTFOLIO PROTECTION: blocking {new_action} @ {new_price:.0f} — you have open {ot.get('action')} @ {ot_entry:.0f} (ID #{ot.get('id')}). Move {price_move_pct:.2f}% insufficient for flip. Current setup kept: {ot.get('action')}. Close position first or wait for stronger invalidation.", ot

        # 2. Check last emitted signal
        if not self.last_emitted:
            # First signal ever — allow
            return False, "First signal — allowed", None

        last_action = self.last_emitted.get("action", "STAND_ASIDE")
        last_dir = _action_direction(last_action)
        last_price = float(self.last_emitted.get("entry_price", 0))
        last_ts_str = self.last_emitted.get("timestamp", "")
        try:
            last_ts = datetime.fromisoformat(last_ts_str)
        except Exception:
            last_ts = datetime.now() - timedelta(minutes=60)

        elapsed_min = (datetime.now() - last_ts).total_seconds() / 60.0

        # Same direction? Check deduplication
        if new_dir == last_dir:
            if last_price > 0 and new_price > 0:
                diff_pct = abs(new_price - last_price) / last_price * 100
                if diff_pct < self.same_thesis_pct:
                    return True, f"Duplicate thesis suppression: {new_action} @ {new_price:.0f} is only {diff_pct:.2f}% from last {last_action} @ {last_price:.0f} (<{self.same_thesis_pct}%). Keeping previous signal to avoid spam.", self.last_emitted
            # Same direction, enough price diff, allow but update
            return False, f"Same direction continuation: {last_action} → {new_action} price diff ok", None

        # Opposite direction — FLIP attempt
        if _opposite(new_dir, last_dir):
            # Time cooldown check
            if elapsed_min < self.cooldown_minutes:
                # Calculate price movement since last signal
                price_move_pct = 0
                if last_price and new_price:
                    # For flip, we care about move in new direction
                    # If last LONG @67000, new SHORT @67100 → price up 0.14% — NOT sufficient
                    price_move_pct = abs(new_price - last_price) / last_price * 100

                # Conditions to ALLOW flip early:
                # - price moved >= flip threshold AND confidence >= min_to_flip AND RR >=1.5
                # Otherwise BLOCK
                if price_move_pct >= self.flip_price_pct and new_conf >= self.min_conf_to_flip and new_rr >= 1.5:
                    # Allow but log
                    return False, f"Flip allowed: price moved {price_move_pct:.2f}% (≥{self.flip_price_pct}%) + conf {new_conf:.0f}% (≥{self.min_conf_to_flip}%) after {elapsed_min:.1f}m cooldown (override)", None
                else:
                    # Suppress flip
                    return True, f"⚠️ SIGNAL STABILITY: blocking flip {last_action} @ {last_price:.0f} → {new_action} @ {new_price:.0f} — only {elapsed_min:.1f}m elapsed (<{self.cooldown_minutes}m cooldown), price moved {price_move_pct:.2f}% (<{self.flip_price_pct}% required), conf {new_conf:.0f}% (<{self.min_conf_to_flip}% required). Keeping {last_action}. This protects you from whipsaw like 3pm LONG 67k → 3:05pm SHORT 67.1k.", self.last_emitted

            # Cooldown passed — still need sanity check on flip frequency
            # (max flips per hour)
            one_hour_ago = datetime.now() - timedelta(hours=1)
            recent_flips = 0
            last_dir_seen = last_dir
            for rec in self.recent_signals:
                try:
                    rt = datetime.fromisoformat(rec.get("timestamp", ""))
                except Exception:
                    continue
                if rt < one_hour_ago:
                    break
                rd = _action_direction(rec.get("action", ""))
                if rd != 0 and rd != last_dir_seen:
                    recent_flips += 1
                    last_dir_seen = rd
            if recent_flips >= DEFAULT_MAX_FLIP_PER_HOUR:
                return True, f"Flip rate-limit: {recent_flips} flips in last hour (max {DEFAULT_MAX_FLIP_PER_HOUR}). Blocking {new_action} @ {new_price:.0f}. Market is choppy — STAND_ASIDE safer.", self.last_emitted

        # Default allow
        return False, "No suppression — new thesis sufficiently different", None

    def update_last_emitted(self, queued_record: Dict):
        """Call after a decision is queued for approval — becomes new anchor for stability."""
        self.last_emitted = queued_record
        self._last_flip_time = datetime.now()

    # ── Query helpers for dashboard ─────────────────────────────────────
    def get_recent_history(self, limit: int = 20) -> List[Dict]:
        return self.recent_signals[:limit]

    def get_stability_status(self) -> Dict:
        """For UI: show current stability guardrails."""
        if not self.last_emitted:
            return {
                "status": "NO_HISTORY",
                "message": "No previous signal — next signal will be anchor.",
                "cooldown_remaining": 0,
                "last_action": None,
            }
        try:
            last_ts = datetime.fromisoformat(self.last_emitted.get("timestamp", ""))
            elapsed = (datetime.now() - last_ts).total_seconds() / 60.0
            remaining = max(0, self.cooldown_minutes - elapsed)
        except Exception:
            elapsed = 999
            remaining = 0

        last_action = self.last_emitted.get("action")
        last_price = self.last_emitted.get("entry_price", 0)

        if remaining > 0:
            return {
                "status": "COOLDOWN_ACTIVE",
                "message": f"Flip protection ACTIVE: {last_action} @ {last_price:.0f} for {remaining:.1f}m more. Opposite signals need {self.flip_price_pct}% move + {self.min_conf_to_flip}% conf.",
                "cooldown_remaining": remaining,
                "last_action": last_action,
                "last_price": last_price,
                "last_timestamp": self.last_emitted.get("timestamp"),
            }
        else:
            return {
                "status": "READY",
                "message": f"Ready for new thesis. Last was {last_action} @ {last_price:.0f} ({elapsed:.1f}m ago).",
                "cooldown_remaining": 0,
                "last_action": last_action,
                "last_price": last_price,
                "last_timestamp": self.last_emitted.get("timestamp"),
            }

    def get_performance_by_action(self) -> Dict:
        """Simple learning: which action types performed best?"""
        if not self.db:
            return {}
        try:
            df = self.db.get_decision_history(limit=500)
            if df.empty:
                return {}
            # Compute win rate per action
            # ai_decisions table holds exit pnl
            stats = {}
            for action in df["action"].unique():
                subset = df[df["action"] == action]
                wins = len(subset[subset["status"] == "WIN"])
                losses = len(subset[subset["status"] == "LOSS"])
                total = wins + losses
                if total > 0:
                    stats[action] = {
                        "wins": wins,
                        "losses": losses,
                        "win_rate": round(wins / total * 100, 1),
                        "total": total,
                    }
            return stats
        except Exception as exc:
            logger.warning(f"get_performance_by_action failed: {exc}")
            return {}
