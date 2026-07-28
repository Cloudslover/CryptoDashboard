"""Macro monitor with independent fallbacks. One unavailable source never stops a refresh."""
from __future__ import annotations
import os, time, logging
from utils.http import get_json
logger = logging.getLogger(__name__)

class MacroMonitor:
    # Includes US, London, Europe, Japan, FX, commodities and volatility.
    INSTRUMENTS = {"SPX":"^GSPC", "NASDAQ":"^IXIC", "US30":"^DJI", "FTSE100":"^FTSE", "DAX":"^GDAXI", "NIKKEI":"^N225", "VIX":"^VIX", "DXY":"DX-Y.NYB", "EURUSD":"EURUSD=X", "USDJPY":"JPY=X", "GOLD":"GC=F", "OIL":"CL=F"}
    def __init__(self): self.cache, self.cache_time = {}, {}
    def _cached(self, key, ttl): return key in self.cache_time and time.time() - self.cache_time[key] < ttl
    def _store(self, key, value): self.cache[key], self.cache_time[key] = value, time.time(); return value

    def get_markets(self):
        if self._cached("markets", 300): return self.cache["markets"]
        data = {}
        for name, symbol in self.INSTRUMENTS.items():
            try:
                raw = get_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", params={"range":"5d", "interval":"1d"}, timeout=8)
                closes = [x for x in raw["chart"]["result"][0]["indicators"]["quote"][0]["close"] if x is not None]
                if len(closes) < 2: raise ValueError("insufficient closes")
                data[name] = {"price": round(closes[-1], 2), "change": round((closes[-1] / closes[-2] - 1) * 100, 2), "available": True}
            except Exception as exc:
                logger.warning("macro source unavailable for %s: %s", name, exc)
                data[name] = {"price": 0, "change": 0, "available": False}
        return self._store("markets", data)

    def get_fed_data(self):
        if self._cached("fed", 3600): return self.cache["fed"]
        fallback = {"fed_funds_rate": None, "yield_10y": None, "yield_change": 0, "signal": "UNAVAILABLE", "interpretation": "Set FRED_API_KEY for official FRED data."}
        key = os.getenv("FRED_API_KEY")
        if not key: return self._store("fed", fallback)
        try:
            endpoint = "https://api.stlouisfed.org/fred/series/observations"
            def series(series_id, limit):
                payload = get_json(endpoint, params={"series_id":series_id,"api_key":key,"file_type":"json","sort_order":"desc","limit":limit}, timeout=10)
                return [float(x["value"]) for x in payload.get("observations", []) if x["value"] not in (".", "")]
            yields, funds = series("DGS10", 5), series("FEDFUNDS", 2)
            y10, change, rate = yields[0], yields[0] - yields[1] if len(yields)>1 else 0, funds[0]
            signal = "BEARISH" if change > .1 else "BULLISH" if change < -.1 else "NEUTRAL"
            return self._store("fed", {"fed_funds_rate":rate,"yield_10y":y10,"yield_change":change,"signal":signal,"interpretation":f"10Y {y10:.2f}% ({change:+.2f}pp)"})
        except Exception as exc:
            logger.warning("FRED unavailable: %s", exc); return self._store("fed", fallback)

    def get_macro_summary(self):
        markets, fed = self.get_markets(), self.get_fed_data()
        def ch(key): return float(markets.get(key, {}).get("change", 0))
        score = 0
        # BTC generally trades as a risk asset in short horizons; relationships can change.
        score += 1 if ch("SPX") > 0 else -1 if ch("SPX") < 0 else 0
        score += 1 if ch("NASDAQ") > 0 else -1 if ch("NASDAQ") < 0 else 0
        score += -2 if markets.get("VIX", {}).get("price", 0) > 25 else 0
        score += -1 if ch("DXY") > .3 else 1 if ch("DXY") < -.3 else 0
        score += -1 if ch("OIL") > 2 else 0
        score += 1 if fed["signal"] == "BULLISH" else -1 if fed["signal"] == "BEARISH" else 0
        bias = "STRONGLY BULLISH" if score >= 3 else "BULLISH" if score >= 1 else "STRONGLY BEARISH" if score <= -3 else "BEARISH" if score <= -1 else "NEUTRAL"
        return {"markets":markets, "stocks":{"indices":markets}, "gold":markets.get("GOLD",{}), "oil":markets.get("OIL",{}), "fed":fed, "macro_score":score, "macro_bias":bias,
                "key_signals":[f"S&P 500 {ch('SPX'):+.2f}%", f"Nasdaq {ch('NASDAQ'):+.2f}%", f"DXY {ch('DXY'):+.2f}%", f"VIX {markets.get('VIX',{}).get('price','N/A')}"]}
