"""BTC AI Brain entry point. Runs analysis continuously but has no exchange execution capability."""
from __future__ import annotations
import logging, threading, time
from config import DASHBOARD_PORT, REFRESH_SECONDS
from utils.logging_config import configure_logging
from data.fetcher import BTCDataFetcher
from data.database import Database
from data.backtester import Backtester
from brain.news_collector import NewsCollector
from brain.macro_monitor import MacroMonitor
from brain.ai_engine import AIBrain
from brain.decision_maker import DecisionManager
from analysis.market_cycle import MarketCycleAnalyzer
from analysis.signals import SignalEngine
from analysis.mtf_analysis import MTFAnalyzer
from dashboard.app import BrainService, create_app
from data.market_intelligence import MarketIntelligence


def main():
    configure_logging()
    log = logging.getLogger(__name__)
    service = BrainService(BTCDataFetcher(), Database(), NewsCollector(), MacroMonitor(), AIBrain(), DecisionManager(), MarketCycleAnalyzer(), SignalEngine(), MTFAnalyzer(), Backtester(), MarketIntelligence())
    service.refresh()
    def monitor():
        while True:
            time.sleep(REFRESH_SECONDS)
            service.refresh()
    threading.Thread(target=monitor, daemon=True, name="market-monitor").start()
    app = create_app(service)
    log.warning("Decision support starts at http://127.0.0.1:%s; no exchange orders can be sent by this application.", DASHBOARD_PORT)
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False)

if __name__ == "__main__": main()
