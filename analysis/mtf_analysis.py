from data.indicators import TechnicalIndicators

class MTFAnalyzer:
    TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d", "1w")
    WEIGHTS = {"1m": .03, "5m": .05, "15m": .1, "1h": .17, "4h": .25, "1d": .25, "1w": .15}

    def analyze_all_timeframes(self, fetcher):
        result, weighted, weight = {}, 0.0, 0.0
        bulls = bears = 0
        for tf in self.TIMEFRAMES:
            df = TechnicalIndicators.add_all_indicators(fetcher.get_ohlcv(tf, limit=300))
            if df.empty:
                result[tf] = {"trend": "No data", "bias": 0, "signal": "WAIT"}
                continue
            row = df.iloc[-1]
            bias = 0
            bias += 2 if row.close > row.ema_200 else -2
            bias += 1 if row.close > row.ema_50 else -1
            bias += 1 if row.macd > row.macd_signal else -1
            bias += 1 if row.supertrend_bull else -1
            rsi = float(row.rsi) if row.rsi == row.rsi else 50
            bias += 1 if rsi > 55 else -1 if rsi < 45 else 0
            label = "BULLISH" if bias >= 2 else "BEARISH" if bias <= -2 else "NEUTRAL"
            result[tf] = {"trend": label, "bias": bias, "rsi": round(rsi, 1), "signal": "LONG BIAS" if bias >= 2 else "SHORT BIAS" if bias <= -2 else "WAIT"}
            weighted += bias * self.WEIGHTS[tf]; weight += self.WEIGHTS[tf]
            bulls += bias >= 2; bears += bias <= -2
        consensus = weighted / weight if weight else 0
        result["consensus"] = {"weighted_bias": round(consensus, 2), "bull_count": bulls, "bear_count": bears,
                                "recommendation": "LONG BIAS" if consensus >= 2 else "SHORT BIAS" if consensus <= -2 else "STAND ASIDE"}
        return result
