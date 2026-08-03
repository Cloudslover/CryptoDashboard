"""Small, credential-free market context adapters for the BTC command center.

These sources are deliberately treated as context, not trading signals. Every method
returns an unavailable payload instead of making the dashboard fail when a public API
rate-limits or changes shape.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from utils.http import get_json

logger = logging.getLogger(__name__)


class MarketIntelligence:
    def __init__(self):
        self.cache: dict[str, Any] = {}
        self.cache_time: dict[str, float] = {}

    def _cached(self, key: str, ttl: int) -> bool:
        return key in self.cache and time.time() - self.cache_time[key] < ttl

    def _store(self, key: str, value: Any) -> Any:
        self.cache[key] = value
        self.cache_time[key] = time.time()
        return value

    def onchain_snapshot(self) -> dict:
        """Return transparent network activity proxies from public Bitcoin APIs."""
        if self._cached("onchain", 120):
            return self.cache["onchain"]
        result = {
            "available": False, "block_height": None, "hashrate": None,
            "mempool_tx_count": None, "mempool_vsize_mb": None,
            "large_mempool_tx": [],
            "explorers": [
                {"name": "mempool.space", "url": "https://mempool.space/"},
                {"name": "Blockstream Explorer", "url": "https://blockstream.info/"},
                {"name": "Blockchain.com", "url": "https://www.blockchain.com/explorer"},
            ],
        }
        try:
            result["block_height"] = int(get_json("https://blockchain.info/q/getblockcount", timeout=8))
            result["hashrate"] = get_json("https://blockchain.info/q/hashrate", timeout=8)
            mempool = get_json("https://mempool.space/api/mempool", timeout=8)
            result["mempool_tx_count"] = int(mempool.get("count", 0))
            result["mempool_vsize_mb"] = round(float(mempool.get("vsize", 0)) / 1_000_000, 2)
            recent = get_json("https://mempool.space/api/mempool/recent", timeout=8)
            # Recent mempool values are BTC satoshis. This is a watchlist, not exchange flow.
            large = [x for x in recent if int(x.get("value", 0)) >= 100 * 100_000_000]
            result["large_mempool_tx"] = [
                {"txid": x.get("txid", ""), "btc": round(int(x.get("value", 0)) / 100_000_000, 2),
                 "fee_sat_vb": x.get("fee", 0)} for x in large[:8]
            ]
            result["available"] = True
        except Exception as exc:
            logger.warning("on-chain sources unavailable: %s", exc)
            result["error"] = str(exc)
        return self._store("onchain", result)

    def exchange_status(self) -> dict:
        """Check public Binance futures health; no credentials or orders are used."""
        if self._cached("exchange_status", 60):
            return self.cache["exchange_status"]
        result = {"exchange": "Binance Futures", "available": False, "ping_ms": None,
                  "symbols_loaded": None, "message": "Unavailable"}
        started = time.perf_counter()
        try:
            get_json("https://fapi.binance.com/fapi/v1/ping", timeout=8)
            result["ping_ms"] = round((time.perf_counter() - started) * 1000)
            info = get_json("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=12)
            result["symbols_loaded"] = len(info.get("symbols", []))
            result.update(available=True, message="Operational")
        except Exception as exc:
            logger.warning("exchange status unavailable: %s", exc)
            result["message"] = str(exc)
        return self._store("exchange_status", result)

    @staticmethod
    def research_links() -> dict[str, list[dict[str, str]]]:
        """Stable launch links that make the former broad OSINT page actionable."""
        return {
            "economic_calendar": [
                {"name": "Federal Reserve calendar", "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"},
                {"name": "BLS release calendar", "url": "https://www.bls.gov/schedule/news_release/"},
                {"name": "CME FedWatch", "url": "https://www.cmegroup.com/markets/interest-rates/cme-fedwatch-tool.html"},
            ],
            "institutional": [
                {"name": "SEC EDGAR", "url": "https://www.sec.gov/edgar/search/"},
                {"name": "Farside ETF flows", "url": "https://farside.co.uk/btc/"},
                {"name": "CoinGlass derivatives", "url": "https://www.coinglass.com/"},
            ],
            "sentiment": [
                {"name": "Fear & Greed", "url": "https://alternative.me/crypto/fear-and-greed-index/"},
                {"name": "Google Trends BTC", "url": "https://trends.google.com/trends/explore?q=bitcoin"},
                {"name": "Reddit r/Bitcoin", "url": "https://www.reddit.com/r/Bitcoin/"},
            ],
        }
