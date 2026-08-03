"""Polymarket prediction-market intelligence (decision support only).

Polymarket prices are real-money implied probabilities, so they act as a
"what informed participants are actually pricing" consensus layer for crypto
and macro events (Fed cuts, CPI, recession odds, Bitcoin price targets, ETF and
policy milestones).

This module reads public, unauthenticated Polymarket data (Gamma API), tracks
how implied probabilities move over time ("shift since ~1 day ago"), flags the
biggest probability shifts, and correlates them with BTC price direction. It is
deliberately NOT a signal generator: it never trades and never recommends a
direction on its own. Like every other provider in this app, it degrades
gracefully and keeps the last successful snapshot during outages.

API notes:
  * Base: https://gamma-api.polymarket.com
  * /public-search?q=... returns markets (and nested events/markets).
  * /markets?slug=... returns a list for a specific market.
  * `outcomes`, `outcomePrices` and `clobTokenIds` are JSON-encoded *strings*
    and must be parsed a second time. Index 0 is usually "Yes".
  * A custom User-Agent avoids intermittent blocks from the Gamma API.
"""
from __future__ import annotations
import json, time, logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Optional

import pandas as pd

from config import (POLYMARKET_REFRESH_SECONDS, POLYMARKET_SHIFT_HOURS,
                    POLYMARKET_SLUGS)
from utils.http import get_json

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
USER_AGENT = "BTC-AI-Brain/1.0 (decision-support research)"

# Keyword -> category used for durable discovery. Specific monthly Polymarket
# slugs rotate/expire, so keyword search is more resilient than a fixed list.
SEARCH_QUERIES = {
    "MACRO":   ["fed", "federal reserve", "cpi", "inflation"],
    "CRYPTO":  ["bitcoin", "ethereum", "crypto"],
    "POLICY":  ["bitcoin etf", "bitcoin reserve", "crypto regulation"],
    "RISK":    ["recession", "us recession", "stock market"],
}


@dataclass
class PolymarketMarket:
    slug: str
    question: str
    category: str
    yes_price: float                       # implied probability as percent (0-100)
    last_trade_price: float                # 0-1 share price
    volume: float
    liquidity: float
    end_date: Optional[str]
    active: bool
    closed: bool
    change_1d: Optional[float] = None      # percentage-point shift vs baseline


class PolymarketMonitor:
    def __init__(self):
        self.cache = {}
        self.cache_time = {}

    def _cached(self, key, ttl):
        return key in self.cache_time and time.time() - self.cache_time[key] < ttl

    def _store(self, key, value):
        self.cache[key] = value
        self.cache_time[key] = time.time()
        return value

    # ── Parsing helpers ────────────────────────────────────────────────────
    @staticmethod
    def _json_list(val):
        """Gamma ships arrays inside JSON strings; handle both forms."""
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                return []
        return []

    def _parse_market(self, m, category) -> Optional[PolymarketMarket]:
        if not isinstance(m, dict) or not m.get("slug"):
            return None
        if m.get("closed"):
            return None
        outcomes = self._json_list(m.get("outcomes"))
        prices = self._json_list(m.get("outcomePrices"))
        yes_idx = next((i for i, o in enumerate(outcomes)
                        if str(o).strip().lower() == "yes"), 0)
        try:
            yes = float(prices[yes_idx])
        except (TypeError, ValueError, IndexError):
            try:
                yes = float(m.get("lastTradePrice") or 0)
            except (TypeError, ValueError):
                yes = 0.0
        if not 0 < yes <= 1:
            return None
        try:
            last = float(m.get("lastTradePrice") or yes)
        except (TypeError, ValueError):
            last = yes
        return PolymarketMarket(
            slug=m["slug"],
            question=m.get("question") or "",
            category=category,
            yes_price=round(yes * 100, 2),
            last_trade_price=round(last, 4),
            volume=self._num(m.get("volume")),
            liquidity=self._num(m.get("liquidity")),
            end_date=m.get("endDate"),
            active=bool(m.get("active")),
            closed=bool(m.get("closed")),
        )

    @staticmethod
    def _num(v) -> float:
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _extract_markets(data) -> list:
        """Normalise /public-search responses (dict with nested lists) or /markets (list)."""
        out = []
        if isinstance(data, list):
            out.extend(data)
        elif isinstance(data, dict):
            out.extend(data.get("markets") or [])
            for ev in data.get("events") or []:
                out.extend(ev.get("markets") or [])
        return out

    # ── Fetching ───────────────────────────────────────────────────────────
    def fetch(self) -> List[PolymarketMarket]:
        markets: List[PolymarketMarket] = []
        seen = set()
        for category, queries in SEARCH_QUERIES.items():
            for q in queries:
                try:
                    data = get_json(
                        f"{GAMMA_BASE}/public-search",
                        params={"q": q, "limit": 20},
                        timeout=10, headers={"User-Agent": USER_AGENT})
                    for m in self._extract_markets(data):
                        mk = self._parse_market(m, category)
                        if mk and mk.slug not in seen:
                            seen.add(mk.slug)
                            markets.append(mk)
                except Exception as exc:
                    logger.warning("polymarket search '%s' unavailable: %s", q, exc)
        # Optional curated slugs (exact match), added only if not already present.
        slugs = [s.strip() for s in (POLYMARKET_SLUGS or "").split(",") if s.strip()]
        for slug in slugs:
            if slug in seen:
                continue
            try:
                data = get_json(f"{GAMMA_BASE}/markets",
                                params={"slug": slug},
                                timeout=10, headers={"User-Agent": USER_AGENT})
                for m in self._extract_markets(data):
                    mk = self._parse_market(m, "CURATED")
                    if mk and mk.slug not in seen:
                        seen.add(mk.slug)
                        markets.append(mk)
            except Exception as exc:
                logger.warning("polymarket curated slug '%s' unavailable: %s", slug, exc)
        return markets

    # ── Baseline / shift computation ───────────────────────────────────────
    def _baseline_map(self, db) -> Dict[str, float]:
        """Latest recorded implied probability for each slug at-or-before ~SHIFT_HOURS ago."""
        baseline = {}
        try:
            hist = db.load_polymarket_history()
        except Exception as exc:
            logger.warning("polymarket history unavailable: %s", exc)
            return baseline
        if hist is None or hist.empty:
            return baseline
        cutoff = datetime.now() - timedelta(hours=POLYMARKET_SHIFT_HOURS)
        try:
            hist["_ts"] = pd.to_datetime(hist["timestamp"], errors="coerce")
        except Exception:
            return baseline
        for slug, grp in hist.groupby("slug"):
            prev = grp[grp["_ts"] <= cutoff]
            if not prev.empty:
                row = prev.sort_values("_ts").iloc[-1]
                try:
                    baseline[slug] = float(row["yes_price"])
                except (TypeError, ValueError):
                    continue
        return baseline

    def build_summary(self, markets, baseline_map=None, btc_change_pct=None) -> Dict:
        baseline_map = baseline_map or {}
        summary_markets = []
        for m in markets:
            base = baseline_map.get(m.slug)
            m.change_1d = round(m.yes_price - base, 2) if base is not None else None
            summary_markets.append({
                "slug": m.slug, "question": m.question, "category": m.category,
                "yes_price": m.yes_price, "change_1d": m.change_1d,
                "volume": m.volume, "liquidity": m.liquidity, "end_date": m.end_date,
            })

        def avg(cat):
            vals = [x["change_1d"] for x in summary_markets
                    if x["category"] == cat and x["change_1d"] is not None]
            return round(sum(vals) / len(vals), 2) if vals else 0.0

        macro = avg("MACRO"); crypto = avg("CRYPTO")
        policy = avg("POLICY"); risk = avg("RISK")

        shifts = [x for x in summary_markets if x["change_1d"] is not None]
        shifts.sort(key=lambda x: abs(x["change_1d"]), reverse=True)
        biggest = shifts[:5]

        net = crypto * 1.0 + macro * 0.6 + policy * 0.8 + risk * 0.5
        bias = ("BULLISH" if net >= 1.5 else "BEARISH" if net <= -1.5
                else "NEUTRAL")

        correlation = ("Not enough data to correlate yet (need a baseline snapshot)."
                       if crypto == 0 else
                       "No live BTC change available to compare."
                       if btc_change_pct is None else
                       "Aligned: crypto probability moves and BTC moved the same way."
                       if (crypto > 0) == (btc_change_pct > 0)
                       else "Conflict: crypto probability moved while BTC was flat/opposite "
                            "— probabilities repriced faster than price.")

        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "markets": summary_markets,
            "biggest_shifts": biggest,
            "macro_prob_shift": macro,
            "crypto_prob_shift": crypto,
            "policy_prob_shift": policy,
            "risk_prob_shift": risk,
            "net_shift_pts": round(net, 2),
            "overall_bias": bias,
            "correlation_note": correlation,
            "source": "polymarket_gamma",
        }

    # ── Public API ─────────────────────────────────────────────────────────
    def get_summary(self, db, btc_change_pct=None) -> Dict:
        """Fetch, compute shifts, persist, and return the Polymarket summary.

        Returns the last good snapshot during an outage so the dashboard is
        never blanked by a temporary provider failure.
        """
        if self._cached("summary", POLYMARKET_REFRESH_SECONDS):
            return self.cache["summary"]
        markets = self.fetch()
        baseline = self._baseline_map(db)
        summary = self.build_summary(markets, baseline, btc_change_pct)
        if markets:
            try:
                db.save_polymarket(markets)
            except Exception as exc:
                logger.warning("polymarket persist failed: %s", exc)
            return self._store("summary", summary)
        # Nothing returned by provider: keep last known snapshot.
        if "summary" in self.cache:
            logger.info("polymarket fetch empty; retaining last snapshot")
            return self.cache["summary"]
        return self._store("summary", summary)
