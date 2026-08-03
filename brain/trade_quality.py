"""Explainable Trade Quality scoring.

Instead of telling the trader to buy/sell, the dashboard scores how strong the
*evidence* is for the current setup across a set of independent factors, then
rolls those up into an overall Trade Quality score (0-100). Each factor is
scored 0-10 with a short human-readable reason, so the score is auditable
rather than a black box.

Weights sum to 1.0 and reflect that market structure/trend and risk matter
more than any single sentiment input.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# Factor -> weight (sum = 1.0). Update both lists together.
FACTOR_WEIGHTS = {
    "market_structure": 0.14,
    "trend":            0.13,
    "volume":           0.09,
    "funding":          0.10,
    "open_interest":    0.08,
    "whale_activity":   0.08,
    "news_risk":        0.09,
    "macro_alignment":  0.10,
    "sentiment":        0.08,
    "risk_reward":      0.11,
}

# Human-friendly display names (replace "Risk Reward" with "Risk/Reward", etc.)
FACTOR_NAMES = {
    "market_structure": "Market Structure",
    "trend":            "Trend",
    "volume":           "Volume",
    "funding":          "Funding",
    "open_interest":    "Open Interest",
    "whale_activity":   "Whale Activity",
    "news_risk":        "News Risk",
    "macro_alignment":  "Macro Alignment",
    "sentiment":        "Sentiment",
    "risk_reward":      "Risk/Reward",
}


class TradeQualityScorer:
    """Scores a setup's trade quality using the same data the AI consumes."""

    def score(self, decision, df, signals, mtf_data, macro_data,
              news_summary, funding, oi, cvd, polymarket_data=None) -> dict:
        factors = {}
        scores = {}

        scores["market_structure"], reason = self._structure(df, decision)
        factors["market_structure"] = reason

        scores["trend"], reason = self._trend(mtf_data)
        factors["trend"] = reason

        scores["volume"], reason = self._volume(df)
        factors["volume"] = reason

        scores["funding"], reason = self._funding(funding, decision)
        factors["funding"] = reason

        scores["open_interest"], reason = self._open_interest(oi)
        factors["open_interest"] = reason

        scores["whale_activity"], reason = self._whale(cvd, df)
        factors["whale_activity"] = reason

        scores["news_risk"], reason = self._news_risk(news_summary)
        factors["news_risk"] = reason

        scores["macro_alignment"], reason = self._macro_alignment(macro_data)
        factors["macro_alignment"] = reason

        scores["sentiment"], reason = self._sentiment(cvd, polymarket_data)
        factors["sentiment"] = reason

        scores["risk_reward"], reason = self._risk_reward(decision)
        factors["risk_reward"] = reason

        total = sum(scores[k] * FACTOR_WEIGHTS[k] for k in FACTOR_WEIGHTS)
        total = round(total, 1)

        label = ("Strong" if total >= 75 else
                 "Good" if total >= 60 else
                 "Average" if total >= 45 else
                 "Poor" if total >= 30 else "Weak")

        return {
            "total": total,
            "label": label,
            "factors": [
                {
                    "key": k,
                    "name": FACTOR_NAMES[k],
                    "score": round(scores[k], 1),
                    "out_of": 10,
                    "weight": FACTOR_WEIGHTS[k],
                    "reason": factors[k],
                }
                for k in FACTOR_WEIGHTS
            ],
        }

    # ── per-factor scorers (0-10, higher = better setup) ──────────────────
    @staticmethod
    def _structure(df, decision):
        # Reward clear structure: price holding above key EMA + a sane stop distance.
        if df is None or df.empty:
            return 5.0, "No structure data"
        try:
            last = df.iloc[-1]
            close = float(last.close)
            score = 5.0
            reasons = []
            for col in ("ema_50", "ema_200"):
                if col in df.columns and last.get(col) == last.get(col):
                    above = close > float(last[col])
                    # Align structure with intended direction when available
                    long = "LONG" in decision.action
                    short = "SHORT" in decision.action
                    if long and above:
                        score += 1.0; reasons.append(f"above {col}")
                    elif short and not above:
                        score += 1.0; reasons.append(f"below {col}")
                    else:
                        score -= 0.5
            # stop distance sanity (not absurdly far/tight)
            if decision and decision.stop_loss:
                dist = abs(decision.entry_price - decision.stop_loss) / max(decision.entry_price, 1)
                if 0.005 <= dist <= 0.03:
                    score += 0.5; reasons.append("healthy stop distance")
                elif dist > 0.05:
                    score -= 1.0; reasons.append("wide stop")
            return max(0.0, min(10.0, score)), ("; ".join(reasons) or "Neutral structure")
        except Exception:
            return 5.0, "Structure unavailable"

    @staticmethod
    def _trend(mtf_data):
        if not isinstance(mtf_data, dict):
            return 5.0, "No trend data"
        con = mtf_data.get("consensus", {}) if isinstance(mtf_data, dict) else {}
        wb = float(con.get("weighted_bias", 0))
        score = 5.0 + wb
        reason = f"weighted MTF bias {wb:+.2f}"
        return max(0.0, min(10.0, score)), reason

    @staticmethod
    def _volume(df):
        if df is None or df.empty or "volume_ratio" not in df.columns:
            return 5.0, "No volume data"
        vr = float(df.iloc[-1].get("volume_ratio", 1.0))
        score = 5.0
        if vr >= 1.5: score += 3.0
        elif vr >= 1.0: score += 1.5
        elif vr >= 0.8: score += 0.5
        else: score -= 1.5
        return max(0.0, min(10.0, score)), f"vol ratio {vr:.2f}x avg"

    @staticmethod
    def _funding(funding, decision):
        if not isinstance(funding, dict):
            return 5.0, "No funding data"
        rate = float(funding.get("current", 0))
        long = "LONG" in decision.action
        # For longs, mildly positive funding is normal; very high is overheated.
        if long:
            if rate > 0.03: score = 3.0; tag = "funding overheated"
            elif rate > 0.01: score = 6.0; tag = "mild positive funding"
            elif rate < -0.01: score = 7.0; tag = "negative funding (short pain)"
            else: score = 6.0; tag = "neutral funding"
        else:  # short
            if rate < -0.03: score = 3.0; tag = "funding extreme short"
            elif rate < -0.01: score = 5.0; tag = "short funding"
            elif rate > 0.01: score = 7.0; tag = "positive funding (long pain)"
            else: score = 6.0; tag = "neutral funding"
        return max(0.0, min(10.0, score)), f"{tag} ({rate:+.4f}%)"

    @staticmethod
    def _open_interest(oi):
        if not isinstance(oi, dict):
            return 5.0, "No OI data"
        chg = float(oi.get("change_24h", 0))
        if chg > 10: score = 4.0; tag = "OI exploding (crowded)"
        elif chg > 5: score = 6.0; tag = "OI building"
        elif chg > 0: score = 6.5; tag = "OI rising"
        elif chg > -5: score = 6.0; tag = "OI stable"
        else: score = 7.0; tag = "OI dropping (unwind)"
        return max(0.0, min(10.0, score)), f"{tag} ({chg:+.2f}%/24h)"

    @staticmethod
    def _whale(cvd, df):
        # Whale proxy: strong directional order flow with large total volume.
        if not isinstance(cvd, dict):
            return 5.0, "No order-flow data"
        buy_pressure = float(cvd.get("buy_pressure", 50))
        total = float(cvd.get("total_volume", 0))
        score = 5.0
        if total > 0:
            if buy_pressure >= 70: score += 3.0; tag = "heavy buying flow"
            elif buy_pressure <= 30: score += 2.0; tag = "heavy selling flow"
            elif buy_pressure >= 55: score += 1.0; tag = "mild buying"
            elif buy_pressure <= 45: score += 0.5; tag = "mild selling"
            else: tag = "balanced flow"
            if cvd.get("divergence"): score -= 2.0; tag += ", divergence"
        else:
            tag = "no flow"
        return max(0.0, min(10.0, score)), tag

    @staticmethod
    def _news_risk(news_summary):
        if not isinstance(news_summary, dict):
            return 5.0, "No news data"
        s = float(news_summary.get("overall_sentiment", 0))
        crit_bear = sum(1 for n in news_summary.get("critical_news", [])
                        if getattr(n, "impact", "") == "CRITICAL" and getattr(n, "sentiment", 0) < 0)
        score = 5.0 + s * 4.0 - min(3.0, crit_bear * 1.5)
        return max(0.0, min(10.0, score)), (f"news sentiment {s:+.2f}, {crit_bear} critical-bear")

    @staticmethod
    def _macro_alignment(macro_data):
        if not isinstance(macro_data, dict):
            return 5.0, "No macro data"
        bias = macro_data.get("macro_bias", "NEUTRAL")
        m = {"STRONGLY BULLISH": 8.5, "BULLISH": 7.0, "NEUTRAL": 5.0,
             "BEARISH": 3.5, "STRONGLY BEARISH": 2.5}
        return m.get(bias, 5.0), f"macro {bias}"

    @staticmethod
    def _sentiment(cvd, polymarket_data):
        score = 5.0
        reasons = []
        if isinstance(cvd, dict):
            bp = float(cvd.get("buy_pressure", 50))
            if bp >= 55: score += 0.75; reasons.append("buying flow")
            elif bp <= 45: score -= 0.75; reasons.append("selling flow")
        if isinstance(polymarket_data, dict):
            bias = polymarket_data.get("overall_bias", "NEUTRAL")
            if bias == "BULLISH": score += 0.75; reasons.append("PM bullish")
            elif bias == "BEARISH": score -= 0.75; reasons.append("PM bearish")
        return max(0.0, min(10.0, score)), ("; ".join(reasons) or "neutral sentiment")

    @staticmethod
    def _risk_reward(decision):
        if not decision or not decision.risk_reward:
            return 5.0, "No R:R"
        rr = float(decision.risk_reward)
        if rr >= 3.0: score = 9.0
        elif rr >= 2.0: score = 8.0
        elif rr >= 1.5: score = 7.0
        elif rr >= 1.0: score = 5.0
        else: score = 3.0
        return max(0.0, min(10.0, score)), f"R:R {rr:.2f}"
