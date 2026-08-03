"""BTC AI Brain entry point — with Brain Memory & Portfolio Guardian."""
from __future__ import annotations
import logging, threading, time
from config import (
    DASHBOARD_PORT,
    REFRESH_SECONDS,
    MAX_RISK_PERCENT,
    MAX_TOTAL_RISK_PERCENT,
    MAX_OPEN_TRADES,
    DAILY_LOSS_LIMIT_PCT,
    MAX_LEVERAGE,
    SIGNAL_COOLDOWN_MINUTES,
    FLIP_PRICE_THRESHOLD_PCT,
    MIN_CONF_TO_FLIP,
    SAME_THESIS_PCT,
)
from utils.logging_config import configure_logging
from data.fetcher import BTCDataFetcher
from data.database import Database
from data.backtester import Backtester
from brain.news_collector import NewsCollector
from brain.macro_monitor import MacroMonitor
from brain.polymarket import PolymarketMonitor
from brain.ai_engine import AIBrain
from brain.decision_maker import DecisionManager
from brain.memory import BrainMemory
from brain.portfolio import PortfolioGuardian
from config import LLM_PROVIDER, LLM_REFRESH_SECONDS
from analysis.market_cycle import MarketCycleAnalyzer
from analysis.signals import SignalEngine
from analysis.mtf_analysis import MTFAnalyzer
from dashboard.app import BrainService, create_app
from data.market_intelligence import MarketIntelligence


def main():
    configure_logging()
    log = logging.getLogger(__name__)

    db = Database()

    # Brain Memory & Portfolio Guardian wired first so they load history from DB
    brain_memory = BrainMemory(
        db=db,
        cooldown_minutes=SIGNAL_COOLDOWN_MINUTES,
        flip_price_pct=FLIP_PRICE_THRESHOLD_PCT,
        min_conf_to_flip=MIN_CONF_TO_FLIP,
        same_thesis_pct=SAME_THESIS_PCT,
    )
    portfolio = PortfolioGuardian(
        db=db,
        max_risk_per_trade=MAX_RISK_PERCENT,
        max_total_risk=MAX_TOTAL_RISK_PERCENT,
        max_open_trades=MAX_OPEN_TRADES,
        daily_loss_limit_pct=DAILY_LOSS_LIMIT_PCT,
        max_leverage=MAX_LEVERAGE,
    )

    decisions = DecisionManager(db=db, portfolio_guardian=portfolio, brain_memory=brain_memory)

    service = BrainService(
        BTCDataFetcher(),
        db,
        NewsCollector(),
        MacroMonitor(),
        PolymarketMonitor(),
        AIBrain(),
        decisions,
        MarketCycleAnalyzer(),
        SignalEngine(),
        MTFAnalyzer(),
        Backtester(),
        MarketIntelligence(),
    )
    # Ensure service uses same instances (BrainService creates its own but we override)
    service.brain_memory = brain_memory
    service.portfolio = portfolio
    service.decisions.portfolio_guardian = portfolio
    service.decisions.brain_memory = brain_memory

    service.refresh()
    service.refresh_llm(force=True)  # first AI Brain brief right after first snapshot

    def monitor():
        while True:
            time.sleep(REFRESH_SECONDS)
            service.refresh()
            # No-ops until LLM_REFRESH_SECONDS elapse; runs on its own thread.
            service.refresh_llm()

    threading.Thread(target=monitor, daemon=True, name="market-monitor").start()
    app = create_app(service)
    log.warning(
        "BTC.AI BRAIN SECURE TERMINAL — http://127.0.0.1:%s | "
        "BrainMemory cooldown=%sm flip=%.1f%% conf=%.0f%% | "
        "Portfolio max_risk=%.1f%% total=%.1f%% max_open=%s daily_stop=-%.1f%% | "
        "LLM assistant provider=%s every=%ss | NO ORDERS",
        DASHBOARD_PORT,
        SIGNAL_COOLDOWN_MINUTES,
        FLIP_PRICE_THRESHOLD_PCT,
        MIN_CONF_TO_FLIP,
        MAX_RISK_PERCENT,
        MAX_TOTAL_RISK_PERCENT,
        MAX_OPEN_TRADES,
        DAILY_LOSS_LIMIT_PCT,
        LLM_PROVIDER,
        LLM_REFRESH_SECONDS,
    )
    app.run(host="0.0.0.0", port=DASHBOARD_PORT, debug=False)


if __name__ == "__main__":
    main()
