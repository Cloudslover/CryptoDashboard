# brain/ai_engine.py
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class AIDecision:
    timestamp:      datetime
    action:         str
    confidence:     float
    reasoning:      List[str]
    entry_price:    float
    stop_loss:      float
    take_profit_1:  float
    take_profit_2:  float
    take_profit_3:  float
    position_size:  float
    leverage:       int
    time_horizon:   str
    risk_reward:    float
    invalidation:   str
    requires_approval: bool = True


class AIBrain:

    def __init__(self):
        self.history = []
        logger.info("[OK] AIBrain ready")

    def make_decision(self, market_data, signals, mtf_data,
                      news_summary, macro_data, cycle_data,
                      price_history, polymarket_data=None) -> AIDecision:
        reasoning = []
        score     = 0.0

        # 1. MACRO (18%)
        ms, mr = self._macro(macro_data)
        score += ms * 0.18
        reasoning.extend(mr)

        # 2. NEWS (12%)
        ns, nr = self._news(news_summary)
        score += ns * 0.12
        reasoning.extend(nr)

        # 3. CYCLE (18%)
        cs, cr = self._cycle(cycle_data)
        score += cs * 0.18
        reasoning.extend(cr)

        # 4. TECHNICALS (23%)
        ts, tr = self._technicals(signals, mtf_data)
        score += ts * 0.23
        reasoning.extend(tr)

        # 5. PRICE ACTION (19%)
        ps, pr = self._price_action(price_history)
        score += ps * 0.19
        reasoning.extend(pr)

        # 6. PREDICTION MARKETS (10%) — real-money implied-probability sentiment
        pms, pmr = self._polymarket(polymarket_data)
        score += pms * 0.10
        reasoning.extend(pmr)

        # RISK FILTERS
        score, rr = self._risk_filters(score, market_data, signals, macro_data)
        reasoning.extend(rr)

        action, confidence = self._to_action(score)
        price = market_data.get("price", 0) if isinstance(market_data, dict) else 0
        sl, tp1, tp2, tp3, rr_ratio = self._levels(action, price, price_history, mtf_data)
        size, lev = self._position(confidence, cycle_data, signals)
        horizon   = self._horizon(mtf_data, cycle_data)
        invalid   = self._invalidation(action, price, price_history)

        dec = AIDecision(
            timestamp      = datetime.now(),
            action         = action,
            confidence     = confidence,
            reasoning      = reasoning,
            entry_price    = price,
            stop_loss      = sl,
            take_profit_1  = tp1,
            take_profit_2  = tp2,
            take_profit_3  = tp3,
            position_size  = size,
            leverage       = lev,
            time_horizon   = horizon,
            risk_reward    = rr_ratio,
            invalidation   = invalid,
            requires_approval = True,
        )
        self.history.append(dec)
        return dec

    # ── Analyzers ────────────────────────────────────────────────────
    def _macro(self, macro):
        s = 0.0; r = []
        if not macro: return 0, ["Macro: N/A"]
        bias = macro.get("macro_bias","NEUTRAL")
        if "STRONGLY BULLISH" in bias: s+=30; r.append(f"MACRO: {bias}")
        elif "BULLISH" in bias:        s+=15; r.append(f"MACRO: {bias}")
        elif "STRONGLY BEARISH" in bias:s-=30;r.append(f"MACRO DANGER: {bias}")
        elif "BEARISH" in bias:        s-=15; r.append(f"MACRO: {bias}")
        else: r.append("MACRO: Neutral")
        fed = macro.get("fed",{})
        if "BULLISH" in fed.get("signal",""):
            s+=15; r.append(f"FED: {fed.get('interpretation','')}")
        elif "BEARISH" in fed.get("signal",""):
            s-=15; r.append(f"FED WARNING: {fed.get('interpretation','')}")
        stocks = macro.get("stocks",{})
        vix = stocks.get("indices",{}).get("VIX",{})
        vp  = vix.get("price",20) if isinstance(vix,dict) else 20
        if vp > 35: s-=20; r.append(f"VIX DANGER: {vp:.0f}")
        elif vp < 15: s+=10; r.append(f"VIX low ({vp:.0f}) - risk on")
        return s, r

    def _news(self, news):
        s = 0.0; r = []
        if not news: return 0, ["News: N/A"]
        overall = news.get("overall_sentiment",0)
        s = overall * 40
        crit = news.get("critical_news",[])
        for item in crit[:3]:
            if hasattr(item,"impact"):
                if item.impact=="CRITICAL" and item.sentiment>0:
                    s+=20; r.append(f"CRITICAL BULL NEWS: {item.title[:55]}")
                elif item.impact=="CRITICAL" and item.sentiment<0:
                    s-=25; r.append(f"CRITICAL BEAR NEWS: {item.title[:55]}")
                elif item.impact=="HIGH" and item.sentiment>0:
                    s+=10; r.append(f"HIGH BULL: {item.title[:50]}")
                elif item.impact=="HIGH" and item.sentiment<0:
                    s-=12; r.append(f"HIGH BEAR: {item.title[:50]}")
        if not r:
            lbl = "Bullish" if overall>0.1 else "Bearish" if overall<-0.1 else "Neutral"
            r.append(f"News sentiment: {lbl} ({overall:+.2f})")
        return s, r

    def _polymarket(self, data):
        """Score prediction-market sentiment as a supplementary context layer.

        Polymarket prices are real-money implied probabilities. A rise in the
        probability of risk-supportive outcomes (Fed cuts, Bitcoin price
        targets, ETF/reserve milestones) is treated as mildly constructive,
        and a fall as mildly cautious. This is context, not a standalone signal.
        """
        s = 0.0; r = []
        if not data or not data.get("markets"):
            return 0, ["Prediction mkts: N/A"]
        macro  = float(data.get("macro_prob_shift", 0))
        crypto = float(data.get("crypto_prob_shift", 0))
        policy = float(data.get("policy_prob_shift", 0))
        risk   = float(data.get("risk_prob_shift", 0))
        bias   = data.get("overall_bias", "NEUTRAL")
        # Rough alignment: crypto/policy bullish == +, macro/risk cautious flips.
        aligned = crypto * 1.0 + policy * 0.8 - macro * 0.4 - risk * 0.6
        s += float(np.clip(aligned, -25, 25))
        if "BULLISH" in bias:
            r.append(f"PREDICTION MKTS: {bias} bias (crypto {crypto:+.1f}pt, policy {policy:+.1f}pt)")
        elif "BEARISH" in bias:
            r.append(f"PREDICTION MKTS: {bias} bias (crypto {crypto:+.1f}pt, policy {policy:+.1f}pt)")
        else:
            r.append(f"PREDICTION MKTS: Neutral (crypto {crypto:+.1f}pt)")
        for m in (data.get("biggest_shifts") or [])[:2]:
            r.append(f"  {m['question'][:38]} → {m['change_1d']:+.1f}pt")
        return s, r

    def _cycle(self, cycle):
        s=0.0; r=[]
        if not cycle: return 0, ["Cycle: N/A"]
        cs = cycle.get("score",5)
        if cs<=2.5:   s+=35; r.append(f"CYCLE {cs}/10: Deep Accumulation - STRONG LONG")
        elif cs<=4.0: s+=20; r.append(f"CYCLE {cs}/10: Markup - Bullish")
        elif cs<=5.5: s+=5;  r.append(f"CYCLE {cs}/10: Early Markup - Neutral+")
        elif cs<=6.5: s-=10; r.append(f"CYCLE {cs}/10: Distribution starting")
        elif cs<=8.0: s-=25; r.append(f"CYCLE {cs}/10: Distribution DANGER")
        else:         s-=40; r.append(f"CYCLE {cs}/10: Markdown - SHORT only")
        return s, r

    def _technicals(self, signals, mtf):
        s=0.0; r=[]
        if signals:
            gn = sum(1 for x in signals if x.status=="GREEN")
            rd = sum(1 for x in signals if x.status=="RED")
            yn = sum(1 for x in signals if x.status=="YELLOW")
            sig_s = (gn*3 - rd*4) / max(len(signals),1)
            s += sig_s * 10
            r.append(f"SIGNALS: {gn}G {yn}Y {rd}R")
        con  = mtf.get("consensus",{}) if isinstance(mtf,dict) else {}
        bias = con.get("weighted_bias",0)
        bull = con.get("bull_count",0)
        bear = con.get("bear_count",0)
        s += bias * 3
        if bull>=6: r.append(f"MTF ALIGNED: {bull}/7 bullish - HIGH CONVICTION")
        elif bull>=5: r.append(f"MTF: {bull}/7 bullish")
        elif bear>=5: r.append(f"MTF BEARISH: {bear}/7 bearish")
        else: r.append(f"MTF MIXED: {bull}B/{bear}Br")
        return s, r

    def _price_action(self, df):
        s=0.0; r=[]
        if df is None or df.empty or len(df)<20:
            return 0, ["PA: Insufficient data"]
        cur = float(df["close"].iloc[-1])
        if "ema_200" in df.columns:
            e200 = float(df["ema_200"].iloc[-1])
            if cur > e200*1.05:   s+=15; r.append(f"Price {((cur/e200)-1)*100:.1f}% above 200EMA")
            elif cur > e200:       s+=5;  r.append("Price above 200EMA - Bullish")
            elif cur < e200*0.95: s-=20; r.append("Price well below 200EMA - Bear market")
            else:                  s-=5
        if "rsi" in df.columns:
            rsi = float(df["rsi"].iloc[-1])
            if 40<=rsi<=60:  s+=5;  r.append(f"RSI {rsi:.0f} - Healthy zone")
            elif rsi<30:     s+=15; r.append(f"RSI {rsi:.0f} - OVERSOLD entry")
            elif rsi>75:     s-=15; r.append(f"RSI {rsi:.0f} - OVERBOUGHT avoid")
        if "supertrend_bull" in df.columns:
            if df["supertrend_bull"].iloc[-1]:  s+=10; r.append("Supertrend: BULLISH")
            else:                               s-=10; r.append("Supertrend: BEARISH")
        return s, r

    def _risk_filters(self, score, market_data, signals, macro):
        r = []
        funding = market_data.get("funding",{}) if isinstance(market_data,dict) else {}
        rate    = funding.get("current",0) if isinstance(funding,dict) else 0
        if rate>0.05:  score=min(score,-20); r.append("RISK: Funding extremely high!")
        elif rate>0.03:score=min(score, 20); r.append("RISK: Funding elevated - capped")
        if signals:
            rd = sum(1 for x in signals if x.status=="RED")
            if rd>=5: score=min(score,-10); r.append(f"RISK: {rd} red signals active")
        if isinstance(macro,dict):
            stocks = macro.get("stocks",{})
            vix    = stocks.get("indices",{}).get("VIX",{})
            vp     = vix.get("price",20) if isinstance(vix,dict) else 20
            if vp>40: score-=20; r.append(f"RISK: VIX={vp:.0f} - Market panic!")
        return score, r

    def _to_action(self, score):
        score = max(-100, min(100, score))
        if score>=60:   return "STRONG_LONG",  min(95, 60+score*0.35)
        elif score>=30: return "LONG",          min(85, 50+score*0.5)
        elif score>=10: return "WEAK_LONG",     min(70, 40+score)
        elif score>=-10:return "STAND_ASIDE",   50
        elif score>=-30:return "WEAK_SHORT",    min(70, 40-score*0.5)
        elif score>=-60:return "SHORT",         min(85, 50-score*0.5)
        else:           return "STRONG_SHORT",  min(95, 60-score*0.35)

    def _levels(self, action, price, df, mtf):
        atr = float(df["atr"].iloc[-1]) if (df is not None and not df.empty and "atr" in df.columns) else price*0.01
        long = "LONG" in action
        if long:
            sl  = round(price - atr*2, 0)
            tp1 = round(price + atr*2, 0)
            tp2 = round(price + atr*4, 0)
            tp3 = round(price + atr*7, 0)
        else:
            sl  = round(price + atr*2, 0)
            tp1 = round(price - atr*2, 0)
            tp2 = round(price - atr*4, 0)
            tp3 = round(price - atr*7, 0)
        rr = abs(tp1-price)/abs(price-sl) if abs(price-sl)>0 else 0
        return sl, tp1, tp2, tp3, round(rr,2)

    def _position(self, conf, cycle, signals):
        base = 2.0 if conf>=80 else 1.5 if conf>=65 else 1.0 if conf>=50 else 0.5
        cs   = cycle.get("score",5) if isinstance(cycle,dict) else 5
        if cs>7:   base*=0.5
        elif cs>6: base*=0.75
        lev = 5 if cs<=3 else 8 if cs<=5 else 3 if cs<=7 else 2
        return round(base,1), lev

    def _horizon(self, mtf, cycle):
        con  = mtf.get("consensus",{}) if isinstance(mtf,dict) else {}
        bias = abs(con.get("weighted_bias",0))
        cs   = cycle.get("score",5) if isinstance(cycle,dict) else 5
        if bias>6 and cs<6:   return "SWING (1-7 days)"
        elif bias>4:          return "INTRADAY (4-24h)"
        else:                 return "SCALP (1-4h)"

    def _invalidation(self, action, price, df):
        atr = float(df["atr"].iloc[-1]) if (df is not None and not df.empty and "atr" in df.columns) else price*0.01
        if "LONG" in action:
            return f"Price closes below ${price-atr*2.5:,.0f} on 1H"
        elif "SHORT" in action:
            return f"Price closes above ${price+atr*2.5:,.0f} on 1H"
        return "Wait for clear setup"