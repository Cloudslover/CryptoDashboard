# brain/news_collector.py
import feedparser
import time
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)


@dataclass
class NewsItem:
    title:     str
    summary:   str
    source:    str
    category:  str
    url:       str
    timestamp: datetime
    sentiment: float = 0.0
    relevance: float = 0.0
    impact:    str   = "LOW"


class NewsCollector:

    RSS_FEEDS = {
        "CRYPTO": [
            "https://cointelegraph.com/rss",
            "https://coindesk.com/arc/outboundfeeds/rss/",
            "https://bitcoinmagazine.com/feed",
            "https://decrypt.co/feed",
        ],
        "MACRO": [
            "https://feeds.reuters.com/reuters/businessNews",
            "https://rss.nytimes.com/services/xml/rss/nyt/Economy.xml",
        ],
        "FED": [
            "https://www.federalreserve.gov/feeds/press_all.xml",
        ],
        "GEOPOLITICAL": [
            "https://feeds.reuters.com/reuters/worldNews",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
        ],
    }

    BULL_KEYWORDS = [
        "bitcoin etf", "rate cut", "fed dovish", "halving",
        "institutional buying", "btc reserve", "dollar weakens",
        "inflation falls", "bullish", "rally", "surge", "breakout",
        "adoption", "bitcoin legal tender", "risk on", "gold rally",
        "stock market up", "bitcoin accumulation",
    ]

    BEAR_KEYWORDS = [
        "bitcoin ban", "crypto ban", "exchange hack", "sec charges",
        "rate hike", "fed hawkish", "quantitative tightening",
        "dollar strengthens", "crash", "collapse", "selloff",
        "bear market", "risk off", "recession", "war escalation",
        "nuclear", "market crash", "stablecoin depeg",
    ]

    def __init__(self):
        self.cache      = []
        self.last_fetch = {}
        self.interval   = 300
        logger.info("[OK] NewsCollector ready")

    def fetch_all_news(self) -> List[NewsItem]:
        # Preserve the last successful result during a feed cooldown or outage.
        # A temporary broken publisher must not make the intelligence panel empty.
        all_news = []
        for category, feeds in self.RSS_FEEDS.items():
            for url in feeds:
                all_news.extend(self._fetch_rss(url, category))
        if all_news:
            all_news.sort(key=lambda x: x.timestamp, reverse=True)
            deduped = {item.url or item.title: item for item in all_news}
            self.cache = list(deduped.values())[:100]
        return self.cache

    def _fetch_rss(self, url, category):
        if url in self.last_fetch:
            if time.time() - self.last_fetch[url] < self.interval:
                return []
        items = []
        try:
            feed = feedparser.parse(url)
            self.last_fetch[url] = time.time()
            for entry in feed.entries[:8]:
                title   = getattr(entry, "title",   "")
                summary = getattr(entry, "summary", "")
                link    = getattr(entry, "link",    "")
                try:
                    pub = datetime(*entry.published_parsed[:6])
                except Exception:
                    pub = datetime.now()
                if datetime.now() - pub > timedelta(hours=24):
                    continue
                item = NewsItem(
                    title     = title,
                    summary   = summary[:300],
                    source    = url.split("/")[2],
                    category  = self._classify(title + " " + summary),
                    url       = link,
                    timestamp = pub,
                )
                items.append(item)
        except Exception as e:
            logger.exception("_fetch_rss failed: %s", e)
            # Silent fail - news is bonus data
        return items

    def _classify(self, text):
        t = text.lower()
        if any(k in t for k in ["federal reserve","fomc","fed","powell","rate"]):
            return "FED"
        if any(k in t for k in ["war","military","conflict","invasion","sanctions"]):
            return "WAR"
        if any(k in t for k in ["oil","opec","crude"]):
            return "OIL"
        if any(k in t for k in ["gold","silver","precious"]):
            return "GOLD"
        if any(k in t for k in ["s&p","nasdaq","dow","stock","equity"]):
            return "STOCK"
        if any(k in t for k in ["bitcoin","crypto","btc","ethereum"]):
            return "CRYPTO"
        if any(k in t for k in ["inflation","gdp","recession","economy"]):
            return "MACRO"
        return "GENERAL"

    def score_item(self, item: NewsItem) -> NewsItem:
        text  = (item.title + " " + item.summary).lower()
        score = 0.0
        for kw in self.BULL_KEYWORDS:
            if kw in text:
                score += 0.4
        for kw in self.BEAR_KEYWORDS:
            if kw in text:
                score -= 0.5
        btc_words = ["bitcoin","btc","crypto","cryptocurrency"]
        relevance = sum(0.25 for w in btc_words if w in text)
        item.sentiment = max(-1.0, min(1.0, score))
        item.relevance = min(1.0, relevance)
        abs_s = abs(score)
        if abs_s >= 0.8:   item.impact = "CRITICAL"
        elif abs_s >= 0.5: item.impact = "HIGH"
        elif abs_s >= 0.3: item.impact = "MEDIUM"
        else:              item.impact = "LOW"
        return item

    def get_news_summary(self) -> Dict:
        news = self.fetch_all_news()
        news = [self.score_item(n) for n in news]
        if not news:
            return {
                "overall_sentiment": 0,
                "critical_news":     [],
                "all_news":          [],
                "news_count":        0,
                "bull_news":         0,
                "bear_news":         0,
                "by_category":       {},
                "timestamp":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        sents   = [n.sentiment for n in news if n.relevance > 0.2]
        overall = sum(sents) / len(sents) if sents else 0.0
        crit    = [n for n in news if n.impact in ("CRITICAL", "HIGH")]
        bull    = sum(1 for n in news if n.sentiment > 0.2)
        bear    = sum(1 for n in news if n.sentiment < -0.2)
        by_cat  = {}
        for n in news:
            by_cat.setdefault(n.category, []).append(n.sentiment)
        cat_avg = {c: sum(v)/len(v) for c, v in by_cat.items() if v}
        return {
            "overall_sentiment": round(overall, 3),
            "critical_news":     crit[:5],
            "all_news":          news[:20],
            "news_count":        len(news),
            "bull_news":         bull,
            "bear_news":         bear,
            "by_category":       cat_avg,
            "timestamp":         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
