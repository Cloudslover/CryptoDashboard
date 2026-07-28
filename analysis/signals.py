from dataclasses import dataclass

@dataclass
class Signal:
    id: int
    name: str
    current_read: str
    status: str

class SignalEngine:
    def generate_all_signals(self, df, funding, oi, fear_greed, ls_ratio, order_book, etf_flows):
        if df is None or df.empty:
            return []
        last = df.iloc[-1]
        def status(value, bullish=True):
            return "GREEN" if (value if bullish else not value) else "RED"
        price_bull = last.close > last.get("ema_200", last.close)
        rsi = float(last.get("rsi", 50))
        rate = float(funding.get("current", 0))
        oi_change = float(oi.get("change_24h", 0))
        ratio = float(ls_ratio.get("long_short_ratio", 1))
        imbalance = float(order_book.get("imbalance", 0))
        fg = int(fear_greed.get("value", 50))
        return [
            Signal(1, "Price structure", f"${last.close:,.0f} vs 200 EMA", status(price_bull)),
            Signal(2, "Momentum (RSI)", f"RSI {rsi:.1f}", "GREEN" if 40 <= rsi <= 68 else "YELLOW" if rsi < 75 else "RED"),
            Signal(3, "Funding", f"{rate:+.4f}%", "RED" if rate > .03 else "GREEN"),
            Signal(4, "Open interest", f"{oi_change:+.2f}% / 24h", "RED" if oi_change > 10 else "GREEN"),
            Signal(5, "Long/short ratio", f"{ratio:.2f}", "RED" if ratio > 1.5 else "GREEN"),
            Signal(6, "Fear & greed", f"{fg}/100", "RED" if fg >= 80 else "GREEN" if fg <= 70 else "YELLOW"),
            Signal(7, "Order book", f"{imbalance:+.1f}% imbalance", "GREEN" if imbalance >= -5 else "YELLOW"),
            Signal(8, "Supertrend", "Bullish" if bool(last.get("supertrend_bull", True)) else "Bearish", status(bool(last.get("supertrend_bull", True)))),
            Signal(9, "Volume", f"{float(last.get('volume_ratio', 1)):.2f}x average", "GREEN" if float(last.get("volume_ratio", 1)) >= .8 else "YELLOW"),
            Signal(10, "Spot-volume proxy", f"{float(etf_flows.get('volume_change_pct', 0)):+.1f}%", "GREEN" if float(etf_flows.get("volume_change_pct", 0)) >= -20 else "YELLOW"),
        ]
