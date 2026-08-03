"""Market Intelligence — mempool.space + blockchain.com inspired on-chain layer.

Inspired by:
  https://github.com/mempool/mempool
  Blockchain.com explorer mempool monitoring

This module aggregates transparent, credential-free Bitcoin network signals:
- mempool.space: mempool tx count, vsize, fee histogram, projected blocks, recommended fees,
                 difficulty adjustment, hashrate, recent blocks, large tx watch
- blockchain.info fallback: block height, hashrate
- fees & congestion = network stress proxy (not trading signal, but context)

Every method returns an unavailable payload instead of crashing dashboard.
"""

from __future__ import annotations

import logging
import time
from typing import Any, List

from utils.http import get_json

logger = logging.getLogger(__name__)

MEMPOOL_API = "https://mempool.space/api"
BLOCKCHAIN_Q = "https://blockchain.info/q"
BLOCKCHAIN_API = "https://api.blockchain.info"


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

    # ── Main onchain snapshot ──────────────────────────────────────────
    def onchain_snapshot(self) -> dict:
        """Return mempool-inspired network activity snapshot."""
        if self._cached("onchain", 90):
            return self.cache["onchain"]

        result: dict[str, Any] = {
            "available": False,
            "source": "mempool.space + blockchain.info",
            "block_height": None,
            "hashrate": None,
            "difficulty": None,
            "mempool_tx_count": None,
            "mempool_vsize_mb": None,
            "mempool_fees_btc": None,
            "fee_histogram": [],
            "mempool_blocks": [],          # projected blocks (fee tiers) like mempool.space visual
            "fees_recommended": {},        # {fastestFee, halfHourFee, hourFee, economyFee, minimumFee}
            "difficulty_adjustment": {},
            "recent_blocks": [],
            "large_mempool_tx": [],
            "avg_block_fee": None,
            "explorers": [
                {"name": "mempool.space", "url": "https://mempool.space/"},
                {"name": "Blockstream Explorer", "url": "https://blockstream.info/"},
                {"name": "Blockchain.com", "url": "https://www.blockchain.com/explorer"},
            ],
        }

        # 1. Block height (try mempool.space first, fallback blockchain.info)
        try:
            try:
                height = int(get_json(f"{MEMPOOL_API}/blocks/tip/height", timeout=6))
                result["block_height"] = height
            except Exception:
                height = int(get_json(f"{BLOCKCHAIN_Q}/getblockcount", timeout=6))
                result["block_height"] = height
        except Exception as exc:
            logger.warning("block height unavailable: %s", exc)

        # 2. Mempool stats
        try:
            mempool = get_json(f"{MEMPOOL_API}/mempool", timeout=8)
            result["mempool_tx_count"] = int(mempool.get("count", 0))
            result["mempool_vsize_mb"] = round(float(mempool.get("vsize", 0)) / 1_000_000, 2)
            result["mempool_fees_btc"] = round(float(mempool.get("total_fee", 0)) / 1e8, 4) if mempool.get("total_fee") else None
            # fee histogram
            hist = mempool.get("fee_histogram")
            if isinstance(hist, list) and hist:
                # Each entry [fee, vsize]
                trimmed = sorted(hist, key=lambda x: x[0])[:30]
                result["fee_histogram"] = [{"fee": round(x[0], 2), "vsize": int(x[1])} for x in trimmed[-15:]]
            logger.debug(f"mempool: {result['mempool_tx_count']} tx, {result['mempool_vsize_mb']} MB")
        except Exception as exc:
            logger.warning("mempool stats unavailable: %s", exc)

        # 3. Projected mempool blocks (like mempool.space visualization)
        try:
            blocks = get_json(f"{MEMPOOL_API}/v1/fees/mempool-blocks", timeout=8)
            if isinstance(blocks, list):
                # Each block: {blockSize, blockVSize, nTx, totalFees, medianFee, feeRange: [min, ..., max]}
                parsed = []
                for b in blocks[:8]:
                    fee_range = b.get("feeRange", [])
                    parsed.append({
                        "blockSize": b.get("blockSize", 0),
                        "blockVSize": round(float(b.get("blockVSize", 0)) / 1_000_000, 2),
                        "nTx": b.get("nTx", 0),
                        "totalFees": round(float(b.get("totalFees", 0)) / 1e8, 4),
                        "medianFee": round(float(b.get("medianFee", 0)), 2),
                        "feeRange": [round(float(f), 2) for f in fee_range[:7]] if fee_range else [],
                    })
                result["mempool_blocks"] = parsed
        except Exception as exc:
            logger.warning("mempool-blocks unavailable: %s", exc)

        # 4. Recommended fees
        try:
            fees = get_json(f"{MEMPOOL_API}/v1/fees/recommended", timeout=6)
            if isinstance(fees, dict):
                result["fees_recommended"] = {
                    "fastestFee": fees.get("fastestFee", 0),
                    "halfHourFee": fees.get("halfHourFee", 0),
                    "hourFee": fees.get("hourFee", 0),
                    "economyFee": fees.get("economyFee", 0),
                    "minimumFee": fees.get("minimumFee", 0),
                }
        except Exception as exc:
            logger.warning("recommended fees unavailable: %s", exc)

        # 5. Difficulty adjustment
        try:
            diff_adj = get_json(f"{MEMPOOL_API}/v1/difficulty-adjustment", timeout=6)
            if isinstance(diff_adj, dict):
                result["difficulty_adjustment"] = {
                    "progressPercent": round(float(diff_adj.get("progressPercent", 0)), 2),
                    "difficultyChange": round(float(diff_adj.get("difficultyChange", 0)), 2),
                    "estimatedRetargetDate": diff_adj.get("estimatedRetargetDate"),
                    "remainingBlocks": diff_adj.get("remainingBlocks", 0),
                    "remainingTime": diff_adj.get("remainingTime", 0),
                    "previousRetarget": round(float(diff_adj.get("previousRetarget", 0)), 2),
                    "nextRetargetHeight": diff_adj.get("nextRetargetHeight"),
                    "timeAvg": diff_adj.get("timeAvg", 0),
                }
                result["difficulty"] = diff_adj.get("previousRetarget")
        except Exception as exc:
            logger.warning("difficulty adjustment unavailable: %s", exc)

        # 6. Hashrate (mempool or blockchain.info fallback)
        try:
            try:
                hashrate = get_json(f"{MEMPOOL_API}/v1/mining/hashrate/3d", timeout=8)
                # hashrate has currentHashrate etc
                if isinstance(hashrate, dict):
                    result["hashrate"] = hashrate.get("currentHashrate") or hashrate.get("hashrates", [{}])[-1].get("avgHashrate") if hashrate.get("hashrates") else None
                elif isinstance(hashrate, list) and hashrate:
                    result["hashrate"] = hashrate[-1].get("avgHashrate")
            except Exception:
                # fallback blockchain.info
                hr = get_json(f"{BLOCKCHAIN_Q}/hashrate", timeout=6)
                result["hashrate"] = float(hr) if hr else None
        except Exception as exc:
            logger.warning("hashrate unavailable: %s", exc)

        # 7. Recent blocks (last 6)
        try:
            blocks = get_json(f"{MEMPOOL_API}/v1/blocks", params={"limit": 6}, timeout=8) if False else get_json(f"{MEMPOOL_API}/blocks", timeout=8)
            # First endpoint is /api/blocks (returns ~10 blocks)
            if isinstance(blocks, list) and blocks:
                recent = []
                for b in blocks[:6]:
                    recent.append({
                        "height": b.get("height"),
                        "timestamp": b.get("timestamp"),
                        "tx_count": b.get("tx_count"),
                        "size": b.get("size"),
                        "weight": b.get("weight"),
                        "feeRange": b.get("extras", {}).get("feeRange", [])[:3] if b.get("extras") else [],
                    })
                result["recent_blocks"] = recent
                if recent and recent[0].get("tx_count"):
                    # avg fee proxy
                    pass
        except Exception as exc:
            logger.warning("recent blocks unavailable: %s", exc)

        # 8. Large mempool tx watch (≥100 BTC)
        try:
            recent = get_json(f"{MEMPOOL_API}/mempool/recent", timeout=8)
            if isinstance(recent, list):
                large = [x for x in recent if int(x.get("value", 0)) >= 100 * 100_000_000]
                result["large_mempool_tx"] = [
                    {
                        "txid": x.get("txid", ""),
                        "btc": round(int(x.get("value", 0)) / 100_000_000, 2),
                        "fee": x.get("fee", 0),
                        "fee_sat_vb": x.get("fee", 0),
                        "vsize": x.get("vsize", 0),
                    }
                    for x in large[:10]
                ]
        except Exception as exc:
            logger.warning("mempool recent unavailable: %s", exc)

        # Determine availability
        if result["block_height"] or result["mempool_tx_count"] is not None:
            result["available"] = True
        else:
            result["error"] = "All onchain sources failed"

        return self._store("onchain", result)

    # ── Exchange status ────────────────────────────────────────────────
    def exchange_status(self) -> dict:
        if self._cached("exchange_status", 60):
            return self.cache["exchange_status"]
        result = {
            "exchange": "Binance Futures",
            "available": False,
            "ping_ms": None,
            "symbols_loaded": None,
            "message": "Unavailable",
        }
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

    # ── Blockchain.com style transaction lookup helper ─────────────────
    def tx_lookup_url(self, txid: str) -> str:
        return f"https://mempool.space/tx/{txid}"

    def block_lookup_url(self, height_or_hash: str) -> str:
        return f"https://mempool.space/block/{height_or_hash}"

    @staticmethod
    def research_links() -> dict[str, list[dict[str, str]]]:
        return {
            "onchain": [
                {"name": "mempool.space", "url": "https://mempool.space/"},
                {"name": "Blockchain.com Explorer", "url": "https://www.blockchain.com/explorer"},
                {"name": "Blockstream Explorer", "url": "https://blockstream.info/"},
            ],
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
