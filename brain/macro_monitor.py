# brain/macro_monitor.py
import time
from datetime import datetime
import logging
import os

from utils.http import get_json

logger = logging.getLogger(__name__)


class MacroMonitor:

    def __init__(self):
        self.cache      = {}
        self.cache_time = {}
        logger.info("[OK] MacroMonitor ready")

    def _cached(self, key, secs=300):
        return (key in self.cache_time and
                time.time() - self.cache_time[key] < secs)

    def _store(self, key, val):
        self.cache[key]      = val
        self.cache_time[key] = time.time()
        return val

    def get_gold(self) -> dict:
        if self._cached("gold"):
            return self.cache["gold"]
        try:
            url = ("https://api.coingecko.com/api/v3/simple/price"
                   "?ids=gold&vs_currencies=usd&include_24hr_change=true")
            d = get_json(url, timeout=10).get("gold", {})
            return self._store("gold", {
                "price":  d.get("usd",  2000),
                "change": d.get("usd_24h_change", 0),
                "signal": self._gold_signal(d.get("usd_24h_change", 0)),
            })
        except Exception as e:
            logger.exception("gold: %s", e)
            return {"price": 2000, "change": 0, "signal": "NEUTRAL"}

    def _gold_signal(self, chg):
        if chg > 1.5:   return "BULLISH (safe-haven - good for BTC)"
        elif chg > 0.5: return "SLIGHTLY BULLISH"
        elif chg < -1.5:return "BEARISH (risk-off)"
        elif chg < -0.5:return "SLIGHTLY BEARISH"
        return "NEUTRAL"

    def get_oil(self) -> dict:
        if self._cached("oil"):
            return self.cache["oil"]
        try:
            url = ("https://api.coingecko.com/api/v3/simple/price"
                   "?ids=wtico&vs_currencies=usd&include_24hr_change=true")
            d = get_json(url, timeout=10).get("wtico", {})
            return self._store("oil", {
                "price":  d.get("usd",  75),
                "change": d.get("usd_24h_change", 0),
                "signal": self._oil_signal(d.get("usd_24h_change", 0)),
            })
        except Exception as e:
            logger.exception("oil: %s", e)
            return {"price": 75, "change": 0, "signal": "NEUTRAL"}

    def get_stock_market(self) -> dict:
        if self._cached("stocks"):
            return self.cache["stocks"]
        headers = {"User-Agent": "Mozilla/5.0"}
        indices = {
            "SPX": "^GSPC",
            "NDX": "^IXIC",
            "VIX": "^VIX",
            "DXY": "DX-Y.NYB",
        }
        results = {}
        for name, sym in indices.items():
            try:
                url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
                       f"{sym}?interval=1d&range=2d")
                d = get_json(url, timeout=8)
                closes = d["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                closes = [x for x in closes if x is not None]
                if len(closes) >= 2:
                    price = closes[-1]
                    prev  = closes[-2]
                    chg   = (price - prev) / prev * 100
                    results[name] = {
                        "price":  round(price, 2),
                        "change": round(chg, 2),
                    }
                time.sleep(0.3)
            except Exception as e:
                logger.exception("%s: %s", name, e)
                results[name] = {"price": 0, "change": 0}

        spx_chg = results.get("SPX", {}).get("change", 0)
        vix     = results.get("VIX", {}).get("price",  20)
        dxy_chg = results.get("DXY", {}).get("change",  0)

        return self._store("stocks", {
            "indices":   results,
            "spx_signal":self._spx_signal(spx_chg),
            "vix_signal":self._vix_signal(vix),
            "dxy_signal":self._dxy_signal(dxy_chg),
            "overall":   self._overall_signal(spx_chg, vix, dxy_chg),
        })

    def _spx_signal(self, chg):
        if chg > 2:     return "STRONG BULLISH for BTC"
        elif chg > 0.5: return "BULLISH for BTC"
        elif chg < -2:  return "STRONG BEARISH for BTC"
        elif chg < -0.5:return "BEARISH for BTC"
        return "NEUTRAL"

    def _vix_signal(self, vix):
        if vix > 35:   return "EXTREME FEAR - Very Bearish"
        elif vix > 25: return "HIGH FEAR - Bearish"
        elif vix > 20: return "ELEVATED - Caution"
        elif vix < 15: return "LOW FEAR - Bullish"
        return "NEUTRAL (VIX 15-20)"

    def _dxy_signal(self, chg):
        if chg > 0.5:   return "BEARISH for BTC (strong dollar)"
        elif chg < -0.5:return "BULLISH for BTC (weak dollar)"
        return "NEUTRAL"

    def _overall_signal(self, spx_chg, vix, dxy_chg):
        s = 0
        if spx_chg > 1:    s += 2
        elif spx_chg > 0:  s += 1
        elif spx_chg < -1: s -= 2
        elif spx_chg < 0:  s -= 1
        if vix < 15:   s += 1
        elif vix > 25: s -= 2
        elif vix > 35: s -= 3
        if dxy_chg < -0.3: s += 1
        elif dxy_chg > 0.3:s -= 1
        if s >= 3:    return "STRONGLY BULLISH for BTC"
        elif s >= 1:  return "BULLISH for BTC"
        elif s <= -3: return "STRONGLY BEARISH for BTC"
        elif s <= -1: return "BEARISH for BTC"
        return "NEUTRAL"

    def get_fed_data(self) -> dict:
        if self._cached("fed", 3600):
            return self.cache["fed"]
        try:
            # Use FRED API key from environment if provided
            fred_key = os.getenv("FRED_API_KEY")
            params = {
                "series_id": "DGS10",
                "sort_order": "desc",
                "limit": 5,
                "file_type": "json",
            }
            if fred_key:
                params["api_key"] = fred_key

            url = "https://api.stlouisfed.org/fred/series/observations"
            d = get_json(url, params=params, timeout=10)
            obs = d.get("observations", [])
            yields = [float(o["value"]) for o in obs
                      if o["value"] not in (".", "")]
            y10  = yields[0] if yields else 4.5
            dy   = yields[0] - yields[1] if len(yields) >= 2 else 0

            params2 = {"series_id": "FEDFUNDS", "sort_order": "desc", "limit": 2, "file_type": "json"}
            if fred_key:
                params2["api_key"] = fred_key
            r2 = get_json(url, params=params2, timeout=10)
            obs2 = r2.get("observations", [])
            ffr  = [float(o["value"]) for o in obs2
                    if o["value"] not in (".", "")]
            rate = ffr[0] if ffr else 5.25

            return self._store("fed", {
                "fed_funds_rate": rate,
                "yield_10y":      y10,
                "yield_change":   dy,
                "signal":         self._fed_signal(rate, dy),
                "interpretation": self._fed_interp(rate, dy),
            })
        except Exception as e:
            logger.exception("fed: %s", e)
            return {
                "fed_funds_rate": 5.25,
                "yield_10y":      4.5,
                "yield_change":   0,
                "signal":         "NEUTRAL",
                "interpretation": "FED data unavailable",
            }

    def _fed_signal(self, rate, dy):
        if rate > 5.0 and dy > 0.1:   return "BEARISH (high rates rising)"
        elif rate > 5.0 and dy < -0.1: return "SLIGHTLY BULLISH (rates peaking?)"
        elif rate < 3.0:               return "BULLISH (low rates)"
        return "NEUTRAL"

    def _fed_interp(self, rate, dy):
        if dy > 0.2:  return f"10Y yield RISING to {rate:.2f}% - HAWKISH"
        elif dy <-0.2:return f"10Y yield FALLING - DOVISH signal"
        return f"Fed Funds Rate: {rate:.2f}% - Stable"

    def get_macro_summary(self) -> dict:
        gold   = self.get_gold()
        oil    = self.get_oil()
        stocks = self.get_stock_market()
        fed    = self.get_fed_data()
        score  = 0
        sigs   = []
        # FED
        if "BULLISH" in fed.get("signal",""):
            score += 2
            sigs.append(f"FED: {fed['signal']}")
        elif "BEARISH" in fed.get("signal",""):
            score -= 2
            sigs.append(f"FED: {fed['signal']}")
        # Stocks
        overall = stocks.get("overall","")
        if "STRONGLY BULLISH" in overall:
            score += 3; sigs.append("STOCKS: Risk-on")
        elif "BULLISH" in overall:
            score += 1
        elif "STRONGLY BEARISH" in overall:
            score -= 3; sigs.append("STOCKS: Risk-off")
        elif "BEARISH" in overall:
            score -= 1
        # Gold
        if "BULLISH" in gold.get("signal",""):  score += 1
        elif "BEARISH" in gold.get("signal",""): score -= 1
        # Oil
        if "BEARISH for BTC" in oil.get("signal",""):  score -= 1
        elif "BULLISH for BTC" in oil.get("signal",""): score += 1

        if score >= 4:    bias = "STRONGLY BULLISH"
        elif score >= 2:  bias = "BULLISH"
        elif score <= -4: bias = "STRONGLY BEARISH"
        elif score <= -2: bias = "BEARISH"
        else:             bias = "NEUTRAL"

        return {
            "gold":        gold,
            "oil":         oil,
            "stocks":      stocks,
            "fed":         fed,
            "macro_score": score,
            "macro_bias":  bias,
            "key_signals": sigs,
        }
