"""Explainable local sentiment scorer. It is deliberately not presented as fact checking."""
from __future__ import annotations

class SentimentAnalyzer:
    LEXICON = {
        "rate cut": .8, "dovish": .7, "etf inflow": .7, "institutional buying": .7,
        "bitcoin reserve": .8, "approval": .4, "rally": .4, "breakout": .4,
        "rate hike": -.8, "hawkish": -.7, "etf outflow": -.6, "exchange hack": -.9,
        "ban": -.9, "liquidation": -.4, "selloff": -.6, "war escalation": -.6,
        "recession": -.5, "risk off": -.5,
    }
    def score(self, text: str) -> float:
        text = (text or "").lower()
        hits = [score for phrase, score in self.LEXICON.items() if phrase in text]
        return max(-1., min(1., sum(hits) / max(1, len(hits))))
    def label(self, value: float) -> str:
        return "BULLISH" if value > .2 else "BEARISH" if value < -.2 else "NEUTRAL"
