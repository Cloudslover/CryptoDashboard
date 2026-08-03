import ccxt
import pandas as pd
import numpy as np
import time
from datetime import datetime
import logging

from utils.http import get_json

logger = logging.getLogger(__name__)

try:
    from config import FUTURES_SYMBOL, SYMBOL, COINGECKO_BASE, CVD_TRADES_LIMIT
except ImportError:
    FUTURES_SYMBOL = "BTC/USDT:USDT"
    SYMBOL         = "BTC/USDT"
    COINGECKO_BASE = "https://api.coingecko.com/api/v3"
    CVD_TRADES_LIMIT = 1000


class BTCDataFetcher:
    def __init__(self):
        self.exchange = ccxt.binance({
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        self.spot_exchange = ccxt.binance({"enableRateLimit": True})
        self.cache      = {}
        self.cache_time = {}
        logger.info("[OK] BTCDataFetcher ready")

    def _cached(self, key, secs=30):
        return (key in self.cache_time and
                time.time() - self.cache_time[key] < secs)

    def _store(self, key, val):
        self.cache[key]      = val
        self.cache_time[key] = time.time()
        return val

    def get_ohlcv(self, timeframe="1h", limit=300, futures=True):
        key  = f"ohlcv_{timeframe}_{futures}"
        secs = 20 if timeframe in ("1m","5m") else 60
        if self._cached(key, secs):
            return self.cache[key]
        try:
            ex  = self.exchange if futures else self.spot_exchange
            sym = FUTURES_SYMBOL if futures else SYMBOL
            raw = ex.fetch_ohlcv(sym, timeframe, limit=limit)
            df  = pd.DataFrame(raw,
                    columns=["timestamp","open","high",
                             "low","close","volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            df = df.astype(float)
            return self._store(key, df)
        except Exception as e:
            # Provider outages are expected; callers use the cached/local fallback.
            logger.warning("get_ohlcv %s unavailable: %s", timeframe, e)
            return self.cache.get(key, pd.DataFrame())

    def get_current_price(self):
        try:
            data = get_json(
                "https://fapi.binance.com/fapi/v1/ticker/price"
                "?symbol=BTCUSDT", timeout=5)
            return float(data["price"])
        except Exception as e:
            logger.exception("get_current_price: %s", e)
            return 0.0

    def get_24h_ticker(self):
        try:
            d = get_json(
                "https://fapi.binance.com/fapi/v1/ticker/24hr"
                "?symbol=BTCUSDT", timeout=10)
            return {
                "price":      float(d["lastPrice"]),
                "change_pct": float(d["priceChangePercent"]),
                "change_usd": float(d["priceChange"]),
                "high_24h":   float(d["highPrice"]),
                "low_24h":    float(d["lowPrice"]),
                "volume_btc": float(d["volume"]),
                "volume_usd": float(d["quoteVolume"]),
                "trades":     int(d["count"]),
            }
        except Exception as e:
            logger.exception("get_24h_ticker: %s", e)
            return {"price":0,"change_pct":0,"change_usd":0,
                    "high_24h":0,"low_24h":0,
                    "volume_btc":0,"volume_usd":0,"trades":0}

    def get_funding_rate(self):
        try:
            d = get_json(
                "https://fapi.binance.com/fapi/v1/premiumIndex"
                "?symbol=BTCUSDT", timeout=10)
            rate = float(d.get("lastFundingRate", 0)) * 100
            ann  = rate * 3 * 365
            try:
                hist = get_json(
                    "https://fapi.binance.com/fapi/v1/fundingRate"
                    "?symbol=BTCUSDT&limit=10", timeout=10)
                hist_vals = [float(x["fundingRate"]) * 100 for x in hist]
                avg  = float(np.mean(hist_vals)) if hist_vals else 0.0
            except Exception:
                avg = rate
            return {
                "current":    rate,
                "average_10": avg,
                "mark_price": float(d.get("markPrice",  0)),
                "index_price":float(d.get("indexPrice", 0)),
                "annualized": ann,
                "status":     self._clf_funding(rate),
            }
        except Exception as e:
            logger.exception("get_funding_rate: %s", e)
            return {"current":0,"average_10":0,"annualized":0,
                    "mark_price":0,"index_price":0,"status":"Unknown"}

    def _clf_funding(self, r):
        if r > 0.05:   return "Extremely Overheated"
        if r > 0.01:   return "Overheated Longs"
        if r > 0:      return "Slightly Positive"
        if r > -0.01:  return "Neutral"
        return "Extreme Short Risk"

    def get_open_interest(self):
        try:
            d = get_json(
                "https://fapi.binance.com/fapi/v1/openInterest"
                "?symbol=BTCUSDT", timeout=10)
            cur = float(d["openInterest"])
            c1 = c24 = 0.0
            try:
                r2 = get_json(
                    "https://fapi.binance.com/futures/data/"
                    "openInterestHist?symbol=BTCUSDT"
                    "&period=5m&limit=288", timeout=10)
                df  = pd.DataFrame(r2)
                df["sumOpenInterest"] = df["sumOpenInterest"].astype(float)
                if len(df) > 13:
                    c1 = ((cur - df["sumOpenInterest"].iloc[-13]) /
                          df["sumOpenInterest"].iloc[-13] * 100)
                if len(df) > 0:
                    c24= ((cur - df["sumOpenInterest"].iloc[0]) /
                          df["sumOpenInterest"].iloc[0]  * 100)
            except Exception:
                pass
            price = self.get_current_price()
            return {
                "current_btc": cur,
                "current_usd": cur * price,
                "change_1h":   c1,
                "change_24h":  c24,
                "status":      self._clf_oi(c24),
            }
        except Exception as e:
            logger.exception("get_open_interest: %s", e)
            return {"current_btc":0,"current_usd":0,
                    "change_1h":0,"change_24h":0,"status":"Unknown"}

    def _clf_oi(self, c):
        if c > 10:  return "OI Exploding"
        if c > 5:   return "OI Building"
        if c > 0:   return "OI Rising"
        if c > -5:  return "OI Stable"
        return "OI Dropping"

    def get_liquidations(self):
        default = {
            "long_short_ratio": 1.0,
            "avg_ratio":        1.0,
            "long_pct":        50.0,
            "short_pct":       50.0,
            "status":          "Unknown",
        }
        try:
            raw = get_json(
                "https://fapi.binance.com/futures/data/"
                "takerlongshortRatio?symbol=BTCUSDT"
                "&period=5m&limit=48", timeout=10)

            if not isinstance(raw, list) or len(raw) == 0:
                return default

            df = pd.DataFrame(raw)

            # Auto-detect column names
            ratio_col = long_col = short_col = None
            for col in df.columns:
                cl = col.lower().replace("_","").replace("-","")
                if cl == "longshortratio":
                    ratio_col = col
                elif cl in ("longaccount","buyratio","longratio"):
                    long_col  = col
                elif cl in ("shortaccount","sellratio","shortratio"):
                    short_col = col

            if ratio_col is None:
                # Try computing from long/short
                if long_col and short_col:
                    df["_ratio"] = (df[long_col].astype(float) /
                                    df[short_col].astype(float)
                                    .replace(0, 1))
                    ratio_col = "_ratio"
                else:
                    logger.warning("L/S cols: %s", list(df.columns))
                    return default

            df[ratio_col] = df[ratio_col].astype(float)
            ratio = float(df[ratio_col].iloc[-1])
            avg   = float(df[ratio_col].mean())

            if long_col and short_col:
                lp = float(df[long_col].astype(float).iloc[-1]) * 100
                sp = float(df[short_col].astype(float).iloc[-1])* 100
            else:
                lp = ratio / (1 + ratio) * 100
                sp = 100 - lp

            return {
                "long_short_ratio": ratio,
                "avg_ratio":        avg,
                "long_pct":         lp,
                "short_pct":        sp,
                "status":           self._clf_ls(ratio),
            }
        except Exception as e:
            logger.exception("get_liquidations: %s", e)
            return default

    def _clf_ls(self, r):
        if r > 1.5:  return "Dominated by Longs"
        if r > 1.2:  return "Long Heavy"
        if r > 0.8:  return "Balanced"
        return "Short Heavy"

    def get_fear_greed(self):
        try:
            d = get_json(
                "https://api.alternative.me/fng/?limit=30",
                timeout=10)
            cur = d["data"][0]
            val = int(cur["value"])
            lbl = cur["value_classification"]
            hist= pd.DataFrame(d["data"])
            hist["value"]     = hist["value"].astype(int)
            hist["timestamp"] = pd.to_datetime(
                hist["timestamp"].astype(int), unit="s")
            return {"value":val,"label":lbl,
                    "history":hist,"status":self._clf_fg(val)}
        except Exception as e:
            logger.exception("get_fear_greed: %s", e)
            return {"value":50,"label":"Neutral","status":"Unknown"}

    def _clf_fg(self, v):
        if v >= 80: return "Extreme Greed"
        if v >= 60: return "Greed"
        if v >= 40: return "Neutral"
        if v >= 20: return "Fear"
        return "Extreme Fear"

    def get_order_book_imbalance(self):
        try:
            d = get_json(
                "https://fapi.binance.com/fapi/v1/depth"
                "?symbol=BTCUSDT&limit=100", timeout=10)
            bids = pd.DataFrame(d["bids"],
                    columns=["price","qty"], dtype=float)
            asks = pd.DataFrame(d["asks"],
                    columns=["price","qty"], dtype=float)
            bv   = float((bids["price"]*bids["qty"]).sum())
            av   = float((asks["price"]*asks["qty"]).sum())
            tot  = bv + av
            imb  = (bv-av)/tot*100 if tot > 0 else 0.0
            return {
                "bid_value":     bv,
                "ask_value":     av,
                "imbalance":     imb,
                "bid_ask_ratio": bv/av if av > 0 else 1.0,
                "status": ("Buy Pressure" if imb >  5 else
                            "Sell Pressure" if imb < -5 else
                            "Balanced"),
            }
        except Exception as e:
            logger.exception("get_order_book_imbalance: %s", e)
            return {"bid_value":0,"ask_value":0,"imbalance":0,
                    "bid_ask_ratio":1.0,"status":"Unknown"}

    def get_agg_trades(self, limit=None):
        """Fetch recent aggregated futures trades used for order-flow (CVD) analysis.

        Binance's `m` flag means 'is the buyer the market maker'. If the buyer is
        the maker, the *seller* was the aggressor (sell taker). We use it to
        classify each trade as buy/sell pressure.
        """
        try:
            d = get_json(
                "https://fapi.binance.com/fapi/v1/aggTrades"
                f"?symbol=BTCUSDT&limit={limit}", timeout=10)
            df = pd.DataFrame(d)
            if df.empty:
                return pd.DataFrame(columns=["time","price","qty","is_buyer_maker"])
            df = df.astype({"price": float, "qty": float})
            # is_buyer_maker is a bool string ("true"/"false") from Binance.
            df["is_buyer_maker"] = df["m"].astype(str).str.lower() == "true"
            df["is_buyer_maker"] = df["is_buyer_maker"].astype(bool)
            df["time"] = pd.to_datetime(df["T"], unit="ms")
            return df[["time", "price", "qty", "is_buyer_maker"]]
        except Exception as e:
            logger.warning("get_agg_trades unavailable: %s", e)
            return pd.DataFrame(columns=["time","price","qty","is_buyer_maker"])

    def get_cvd(self, limit=None):
        """Compute order-flow metrics from recent trades: cumulative volume delta,
        net delta, buy/sell pressure split, and a 'delta divergence' flag.

        Returns a plain dict so an unreachable feed degrades to zeros/Unknown.
        """
        limit = limit or CVD_TRADES_LIMIT
        default = {
            "cvd": 0.0, "net_delta": 0.0, "buy_volume": 0.0,
            "sell_volume": 0.0, "total_volume": 0.0, "buy_pressure": 50.0,
            "divergence": False, "status": "Unknown", "n_trades": 0,
        }
        df = self.get_agg_trades(limit)
        if df is None or df.empty:
            return default
        # Seller is taker when the buyer is the maker (m=true).
        # Seller is the taker when the buyer is the maker (m=true).
        sell = df[df["is_buyer_maker"]]["qty"].sum()
        buy = df[~df["is_buyer_maker"]]["qty"].sum()
        total = buy + sell
        if total <= 0:
            return default
        delta = buy - sell  # signed net delta over the window
        # CVD is the running cumulative delta; for a finite window it equals the
        # signed net delta observed in this snapshot.
        return {
            "cvd": round(delta, 4),
            "net_delta": round(delta, 4),
            "buy_volume": round(buy, 4),
            "sell_volume": round(sell, 4),
            "total_volume": round(total, 4),
            "buy_pressure": round(buy / total * 100, 2),
            # Positive price move on negative delta (or vice versa) = divergence.
            "divergence": self._cvd_divergence(df, delta),
            "status": self._clf_cvd(delta / total * 100),
            "n_trades": int(len(df)),
        }

    def _cvd_divergence(self, df, net_delta):
        try:
            if df is None or df.empty or len(df) < 10:
                return False
            first = float(df["price"].iloc[0])
            last = float(df["price"].iloc[-1])
            move = last - first
            # divergence: price up but net selling, or price down but net buying
            return bool((move > 0 and net_delta < 0) or (move < 0 and net_delta > 0))
        except Exception:
            return False

    def _clf_cvd(self, delta_pct):
        if delta_pct > 15:   return "Strong Buying"
        if delta_pct > 5:    return "Buying"
        if delta_pct < -15:  return "Strong Selling"
        if delta_pct < -5:   return "Selling"
        return "Balanced"

    def get_etf_flows(self):
        try:
            d = get_json(
                f"{COINGECKO_BASE}/coins/bitcoin/market_chart"
                "?vs_currency=usd&days=7&interval=daily",
                timeout=15)
            vols = d.get("total_volumes", [])
            if not vols:
                raise ValueError("no volume data")
            df   = pd.DataFrame(vols,
                    columns=["timestamp","volume"])\
                .assign(timestamp=lambda x: pd.to_datetime(x['timestamp'], unit='ms'))
            df["volume"]    = df["volume"].astype(float)
            avg  = float(df["volume"].mean())
            lat  = float(df["volume"].iloc[-1])
            chg  = (lat-avg)/avg*100 if avg > 0 else 0.0
            return {
                "latest_volume":     lat,
                "avg_volume":        avg,
                "volume_change_pct": chg,
                "history":           df,
                "status": ("Positive Flow" if chg >  20 else
                            "Outflow Risk"  if chg < -20 else
                            "Normal Volume"),
            }
        except Exception as e:
            logger.exception("get_etf_flows: %s", e)
            return {"latest_volume":0,"avg_volume":0,
                    "volume_change_pct":0,"status":"Unknown"}
