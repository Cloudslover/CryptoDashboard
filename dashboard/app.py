"""Human-in-the-loop Dash interface — Cyberpunk Hacker Terminal edition.
Approval records a plan only; it cannot place exchange orders.

Major upgrades:
- Brain Memory with signal anti-whipsaw (fixes LONG 67k → SHORT 67.1k in 5min issue)
- Portfolio Guardian (fund protection, risk caps, kill-switch)
- Enhanced mempool.space + blockchain.com on-chain intelligence (mempool blocks visual)
- Full hacker/matrix aesthetic inspired by mempool.space explorer
"""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading, logging, json
import dash
from dash import Dash, dcc, html, Input, Output, dash_table
import plotly.graph_objects as go
import pandas as pd

from config import (
    REFRESH_SECONDS,
    SIGNAL_COOLDOWN_MINUTES,
    FLIP_PRICE_THRESHOLD_PCT,
    MIN_CONF_TO_FLIP,
    SAME_THESIS_PCT,
    MAX_TOTAL_RISK_PERCENT,
    MAX_OPEN_TRADES,
    DAILY_LOSS_LIMIT_PCT,
    MAX_LEVERAGE,
    MAX_RISK_PERCENT,
)
from data.indicators import TechnicalIndicators
from data.market_intelligence import MarketIntelligence
from brain.trade_quality import TradeQualityScorer
from brain.memory import BrainMemory
from brain.portfolio import PortfolioGuardian

logger = logging.getLogger(__name__)

# ── Cyberpunk Palette ────────────────────────────────────────────────
C = {
    "bg": "#050810",
    "bg2": "#080e1e",
    "card": "#0d1426",
    "card2": "#111c33",
    "border": "#00ff41",
    "border_dim": "#1a3a2a",
    "text": "#c8d8f0",
    "text_bright": "#e7f0ff",
    "green": "#00ff41",
    "green_dim": "#00cc33",
    "cyan": "#00e5ff",
    "cyan_dim": "#00aabb",
    "magenta": "#ff00ff",
    "red": "#ff3355",
    "amber": "#ffaa00",
    "gold": "#ffd166",
    "muted": "#5a6a8a",
    "muted2": "#3a4a6a",
}

# ── Cyber CSS — hacker terminal, matrix, scanline, glow ─────────────
CYBER_CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;800&family=Share+Tech+Mono&display=swap');

* {{ scrollbar-width: thin; scrollbar-color: {C['border_dim']} {C['bg']}; }}

body {{
    background: {C['bg']} !important;
    margin: 0 !important;
    font-family: 'JetBrains Mono', 'Share Tech Mono', monospace !important;
}}

::-webkit-scrollbar {{ width: 8px; height: 8px; }}
::-webkit-scrollbar-track {{ background: {C['bg']}; }}
::-webkit-scrollbar-thumb {{ background: {C['border_dim']}; border-radius: 4px; }}
::-webkit-scrollbar-thumb:hover {{ background: {C['green_dim']}; }}

/* Scanline overlay */
body::before {{
    content: "";
    position: fixed;
    top: 0; left: 0; width: 100%; height: 100%;
    background: repeating-linear-gradient(
        0deg,
        rgba(0,255,65,0.03) 0px,
        transparent 1px,
        transparent 2px,
        rgba(0,0,0,0.04) 3px
    );
    pointer-events: none;
    z-index: 9999;
}}

/* Grid bg */
.cyber-bg {{
    background:
        radial-gradient(ellipse at top, rgba(0,255,65,0.08) 0%, transparent 60%),
        radial-gradient(ellipse at bottom right, rgba(0,229,255,0.08) 0%, transparent 60%),
        linear-gradient(180deg, {C['bg']} 0%, {C['bg2']} 100%),
        repeating-linear-gradient(90deg, rgba(0,255,65,0.02) 0 1px, transparent 1px 80px),
        repeating-linear-gradient(0deg, rgba(0,255,65,0.015) 0 1px, transparent 1px 80px);
}}

.cyber-card {{
    background: linear-gradient(135deg, {C['card']} 0%, {C['card2']} 100%) !important;
    border: 1px solid {C['border_dim']} !important;
    border-left: 3px solid {C['green_dim']} !important;
    border-radius: 4px !important;
    box-shadow:
        0 0 20px rgba(0,255,65,0.08),
        inset 0 1px 0 rgba(0,255,65,0.1) !important;
    position: relative;
    overflow: hidden;
    transition: all 0.3s ease;
}}
.cyber-card::before {{
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0; height: 1px;
    background: linear-gradient(90deg, transparent, {C['green']}, transparent);
    opacity: 0.6;
}}
.cyber-card:hover {{
    border-color: {C['green_dim']} !important;
    box-shadow: 0 0 30px rgba(0,255,65,0.15), inset 0 1px 0 rgba(0,255,65,0.15) !important;
}}

/* Corner brackets */
.cyber-card::after {{
    content: "┌┘";
    position: absolute;
    top: 2px; right: 6px;
    color: {C['green']};
    font-size: 10px;
    opacity: 0.4;
    letter-spacing: 2px;
}}

.cyber-header {{
    font-family: 'Share Tech Mono', monospace;
    color: {C['green']};
    text-shadow: 0 0 10px rgba(0,255,65,0.6);
    letter-spacing: 2px;
    text-transform: uppercase;
    border-bottom: 1px dashed {C['border_dim']};
    padding-bottom: 6px;
    margin-bottom: 10px;
}}

.cyber-glow-green {{ color: {C['green']}; text-shadow: 0 0 8px rgba(0,255,65,0.8); }}
.cyber-glow-cyan {{ color: {C['cyan']}; text-shadow: 0 0 8px rgba(0,229,255,0.8); }}
.cyber-glow-amber {{ color: {C['amber']}; text-shadow: 0 0 8px rgba(255,170,0,0.6); }}
.cyber-glow-red {{ color: {C['red']}; text-shadow: 0 0 8px rgba(255,51,85,0.8); }}

.blink {{ animation: blink 1.2s infinite; }}
@keyframes blink {{ 0%,50% {{ opacity:1; }} 51%,100% {{ opacity:0; }} }}

.pulse {{
    animation: pulse 2s infinite;
}}
@keyframes pulse {{
    0% {{ box-shadow: 0 0 0 0 rgba(0,255,65,0.4); }}
    70% {{ box-shadow: 0 0 0 10px rgba(0,255,65,0); }}
    100% {{ box-shadow: 0 0 0 0 rgba(0,255,65,0); }}
}}

.led {{
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    margin-right: 6px;
    box-shadow: 0 0 6px currentColor;
}}
.led-green {{ background: {C['green']}; color: {C['green']}; }}
.led-amber {{ background: {C['amber']}; color: {C['amber']}; }}
.led-red {{ background: {C['red']}; color: {C['red']}; }}
.led-cyan {{ background: {C['cyan']}; color: {C['cyan']}; }}

.mempool-block {{
    display: inline-block;
    width: 42px;
    height: 42px;
    margin: 2px;
    border: 1px solid {C['border_dim']};
    background: linear-gradient(135deg, {C['card']} 0%, rgba(0,255,65,0.1) 100%);
    text-align: center;
    line-height: 42px;
    font-size: 10px;
    color: {C['green']};
    position: relative;
}}
.mempool-block.fee-high {{ border-color: {C['red']}; background: linear-gradient(135deg, rgba(255,51,85,0.2), {C['card']}); color: {C['red']}; }}
.mempool-block.fee-mid {{ border-color: {C['amber']}; background: linear-gradient(135deg, rgba(255,170,0,0.15), {C['card']}); color: {C['amber']}; }}
.mempool-block.fee-low {{ border-color: {C['cyan']}; background: linear-gradient(135deg, rgba(0,229,255,0.12), {C['card']}); color: {C['cyan']}; }}

.terminal-line {{ font-size: 11px; color: {C['muted']}; }}
.terminal-prompt {{ color: {C['green']}; }}

button {{
    background: {C['card']} !important;
    border: 1px solid {C['green_dim']} !important;
    color: {C['green']} !important;
    font-family: 'JetBrains Mono', monospace !important;
    text-transform: uppercase;
    letter-spacing: 1px;
    padding: 6px 14px !important;
    border-radius: 2px !important;
    transition: all 0.2s;
    box-shadow: 0 0 10px rgba(0,255,65,0.15);
}}
button:hover {{
    background: rgba(0,255,65,0.15) !important;
    box-shadow: 0 0 20px rgba(0,255,65,0.4) !important;
    color: {C['text_bright']} !important;
}}

.Select-control, .Select-menu-outer {{
    background: {C['card']} !important;
    border-color: {C['border_dim']} !important;
    color: {C['text']} !important;
}}
.Select-value-label {{ color: {C['green']} !important; }}

.dash-table-container .dash-spreadsheet-container .dash-spreadsheet-inner table {{
    border-collapse: collapse !important;
}}
.dash-header {{ background: {C['card2']} !important; color: {C['cyan']} !important; border-bottom: 1px solid {C['border_dim']} !important; font-size: 11px !important; text-transform: uppercase; letter-spacing: 1px; }}
.dash-cell {{ background: {C['card']} !important; color: {C['text']} !important; border: 1px solid rgba(0,255,65,0.08) !important; font-size: 12px !important; font-family: 'JetBrains Mono', monospace !important; }}
"""


class BrainService:
    def __init__(self, fetcher, db, news, macro, polymarket, brain, decisions, cycle, signals, mtf, backtester, intelligence=None):
        self.fetcher, self.db, self.news, self.macro = fetcher, db, news, macro
        self.polymarket = polymarket
        self.brain, self.decisions, self.cycle, self.signals, self.mtf, self.backtester = brain, decisions, cycle, signals, mtf, backtester
        self.intelligence = intelligence or MarketIntelligence()
        self.trade_quality = TradeQualityScorer()

        # ── NEW: Brain Memory & Portfolio Guardian ───────────────────────
        self.brain_memory = BrainMemory(
            db=self.db,
            cooldown_minutes=SIGNAL_COOLDOWN_MINUTES,
            flip_price_pct=FLIP_PRICE_THRESHOLD_PCT,
            min_conf_to_flip=MIN_CONF_TO_FLIP,
            same_thesis_pct=SAME_THESIS_PCT,
        )
        self.portfolio = PortfolioGuardian(
            db=self.db,
            max_risk_per_trade=MAX_RISK_PERCENT,
            max_total_risk=MAX_TOTAL_RISK_PERCENT,
            max_open_trades=MAX_OPEN_TRADES,
            daily_loss_limit_pct=DAILY_LOSS_LIMIT_PCT,
            max_leverage=MAX_LEVERAGE,
        )
        # inject into decision manager
        if hasattr(self.decisions, "portfolio_guardian"):
            self.decisions.portfolio_guardian = self.portfolio
            self.decisions.brain_memory = self.brain_memory
            self.decisions.db = self.db

        self.lock, self.refresh_lock, self.cache = threading.Lock(), threading.Lock(), {"status": "BOOTING SECURE TERMINAL..."}
        self._news_at = self._macro_at = 0

    def refresh(self):
        if not self.refresh_lock.acquire(blocking=False):
            return
        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                fs = {tf: pool.submit(self.fetcher.get_ohlcv, tf, 500 if tf != "1d" else 730) for tf in ("1h", "4h", "1d")}
                ticker = pool.submit(self.fetcher.get_24h_ticker)
                funding = pool.submit(self.fetcher.get_funding_rate)
                oi = pool.submit(self.fetcher.get_open_interest)
                ls = pool.submit(self.fetcher.get_liquidations)
                fg = pool.submit(self.fetcher.get_fear_greed)
                book = pool.submit(self.fetcher.get_order_book_imbalance)
                cvd = pool.submit(self.fetcher.get_cvd)
                onchain = pool.submit(self.intelligence.onchain_snapshot)
                exchange_status = pool.submit(self.intelligence.exchange_status)
                prev_chg = self.cache.get("ticker", {}).get("change_pct")
                poly = pool.submit(self.polymarket.get_summary, self.db, prev_chg)
                frames = {tf: TechnicalIndicators.add_all_indicators(f.result()) for tf, f in fs.items()}
                ticker, funding, oi, ls, fg, book, cvd = ticker.result(), funding.result(), oi.result(), ls.result(), fg.result(), book.result(), cvd.result()
            onchain, exchange_status = onchain.result(), exchange_status.result()

            for tf, frame in list(frames.items()):
                if frame.empty:
                    frames[tf] = TechnicalIndicators.add_all_indicators(self.db.load_candles(tf))
            if frames["1h"].empty or frames["4h"].empty or frames["1d"].empty or float(ticker.get("price", 0)) <= 0:
                raise RuntimeError("primary BTC market data unavailable; retaining last successful snapshot")

            now = datetime.now().timestamp()
            old = self.cache
            news = old.get("news", {}) if now - self._news_at < 300 else self.news.get_news_summary()
            macro = old.get("macro", {}) if now - self._macro_at < 300 else self.macro.get_macro_summary()
            if news is not old.get("news"):
                self._news_at = now
            if macro is not old.get("macro"):
                self._macro_at = now
            polymarket = poly.result()
            cycle = self.cycle.calculate_cycle_score(frames["1d"], funding, oi, fg, ls)
            signal_list = self.signals.generate_all_signals(frames["1h"], funding, oi, fg, ls, book, {"volume_change_pct": 0}, cvd=cvd)
            mtf = self.mtf.analyze_all_timeframes(self.fetcher)
            market_data = {"price": ticker.get("price", 0), "funding": funding, "oi": oi}
            decision = self.brain.make_decision(
                market_data, signal_list, mtf, news, macro, cycle,
                frames["1h"], polymarket_data=polymarket, cvd=cvd,
                trade_quality_scorer=self.trade_quality
            )

            # ── Brain Memory: record every signal BEFORE filtering
            memory_record = self.brain_memory.record_signal(decision)

            # ── Anti-whipsaw: check if we should suppress flip
            open_trades = self.decisions.get_open_trades_summary() if hasattr(self.decisions, "get_open_trades_summary") else self.portfolio.get_open_summary()
            suppressed, reason, kept = self.brain_memory.should_suppress_flip(decision, open_trades=open_trades)

            stability_info = {
                "suppressed": suppressed,
                "reason": reason,
                "kept_action": kept,
            }

            if suppressed:
                # Do NOT queue new conflicting plan — protect user's portfolio scenario
                queued = {
                    "status": "SUPPRESSED",
                    "reason": reason,
                    "kept_action": kept,
                    "proposed_action": decision.action,
                    "proposed_price": decision.entry_price,
                }
                logger.info(f"BrainMemory suppressed flip: {reason}")
            else:
                # Allow — queue for human approval and update memory anchor
                queued = self.decisions.submit(decision, stability_info=stability_info)
                if queued.get("status") == "PENDING_APPROVAL":
                    self.brain_memory.update_last_emitted(queued if isinstance(queued, dict) else memory_record)

            # Persist candles/news/decisions
            for tf, df in frames.items():
                self.db.save_candles(tf, df)
            if news.get("all_news"):
                self.db.save_news(news["all_news"])
            if queued.get("status") == "PENDING_APPROVAL":
                self.db.save_decision(queued)

            # Additional snapshots
            brain_status = self.brain_memory.get_stability_status()
            portfolio_status = self.portfolio.get_protection_status()
            portfolio_exposure = self.portfolio.get_exposure()

            with self.lock:
                self.cache = {
                    "status": "LIVE",
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "frames": frames,
                    "ticker": ticker,
                    "funding": funding,
                    "oi": oi,
                    "fg": fg,
                    "ls": ls,
                    "book": book,
                    "cvd": cvd,
                    "news": news,
                    "macro": macro,
                    "polymarket": polymarket,
                    "cycle": cycle,
                    "signals": signal_list,
                    "mtf": mtf,
                    "decision": decision,
                    "queued": queued,
                    "stability": stability_info,
                    "brain_status": brain_status,
                    "brain_memory": self.brain_memory.get_recent_history(limit=20),
                    "portfolio_status": portfolio_status,
                    "portfolio_exposure": portfolio_exposure,
                    "portfolio_open": self.portfolio.get_open_summary(),
                    "onchain": onchain,
                    "exchange_status": exchange_status,
                    "research_links": self.intelligence.research_links(),
                }
        except Exception as exc:
            logger.exception("refresh failed")
            with self.lock:
                self.cache = {**self.cache, "status": f"DEGRADED: {exc}"}
        finally:
            self.refresh_lock.release()

    def snapshot(self):
        with self.lock:
            return self.cache.copy()


def _card(children, flex="1", border_color=None, extra_style=None):
    style = {
        "background": f"linear-gradient(135deg, {C['card']} 0%, {C['card2']} 100%)",
        "padding": "14px",
        "borderRadius": "4px",
        "marginBottom": "12px",
        "border": f"1px solid {border_color or C['border_dim']}",
        "borderLeft": f"3px solid {border_color or C['green_dim']}",
        "boxShadow": "0 0 20px rgba(0,255,65,0.08), inset 0 1px 0 rgba(0,255,65,0.08)",
        "flex": flex,
        "position": "relative",
    }
    if extra_style:
        style.update(extra_style)
    return html.Div(children, style=style, className="cyber-card")


def create_app(service: BrainService) -> Dash:
    app = Dash(__name__)
    app.title = "₿ BTC.AI BRAIN // SECURE TERMINAL"
    # Inject cyber CSS via index_string
    app.index_string = f"""
<!DOCTYPE html>
<html>
    <head>
        {{%metas%}}
        <title>{{%title%}}</title>
        {{%favicon%}}
        {{%css%}}
        <style>{CYBER_CSS}</style>
    </head>
    <body class="cyber-bg">
        {{%app_entry%}}
        <footer>
            {{%config%}}
            {{%scripts%}}
            {{%renderer%}}
        </footer>
    </body>
</html>
"""

    app.layout = html.Div(
        [
            dcc.Interval(id="poll", interval=REFRESH_SECONDS * 1000, n_intervals=0),
            # Top status bar — hacker terminal
            html.Div(
                [
                    html.Span("◉", className="led led-green"),
                    html.Span("BTC.AI BRAIN // SECURE TERMINAL", style={"color": C["green"], "fontWeight": "800", "letterSpacing": "2px"}),
                    html.Span(" [root@crypto:~]$ ", style={"color": C["muted"], "marginLeft": "12px"}),
                    html.Span(id="status", style={"color": C["cyan"]}),
                    html.Span(" ▌", className="blink", style={"color": C["green"], "marginLeft": "6px"}),
                    html.Div(
                        [
                            html.Span("MEMPOOL.SPACE", style={"color": C["muted"], "fontSize": "10px", "marginRight": "8px"}),
                            html.Span("BLOCKCHAIN.COM", style={"color": C["muted"], "fontSize": "10px", "marginRight": "8px"}),
                            html.Span("BINANCE", style={"color": C["muted"], "fontSize": "10px"}),
                        ],
                        style={"float": "right", "opacity": "0.6"},
                    ),
                ],
                style={
                    "background": C["card"],
                    "borderBottom": f"1px solid {C['border_dim']}",
                    "padding": "8px 16px",
                    "fontSize": "12px",
                    "fontFamily": "'JetBrains Mono', monospace",
                    "marginBottom": "16px",
                    "position": "sticky",
                    "top": "0",
                    "zIndex": "100",
                    "boxShadow": f"0 2px 20px rgba(0,255,65,0.1)",
                },
            ),
            # Hero + overview
            html.Div(
                [
                    html.Div(id="overview", style={"flex": "1"}),
                    html.Div(id="decision", style={"flex": "2"}),
                    html.Div(id="stability", style={"flex": "1"}),
                ],
                style={"display": "flex", "gap": "12px", "flexWrap": "wrap"},
            ),
            # Portfolio Guardian + Trade Quality + Macro
            html.Div(
                [
                    html.Div(id="portfolio", style={"flex": "1.2"}),
                    html.Div(id="tradequality", style={"flex": "1"}),
                    html.Div(id="macro", style={"flex": "0.8"}),
                ],
                style={"display": "flex", "gap": "12px", "flexWrap": "wrap"},
            ),
            # Derivatives + On-chain mempool visual + Exchange health + Research
            html.Div(
                [
                    html.Div(id="derivatives", style={"flex": "1"}),
                    html.Div(id="onchain", style={"flex": "1.5"}),
                    html.Div(id="health", style={"flex": "0.7"}),
                ],
                style={"display": "flex", "gap": "12px", "flexWrap": "wrap"},
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("▚ BTC FUTURES WORKFLOW", className="cyber-header"),
                            html.Div(
                                "NEWS → MACRO → DERIVATIVES → ON-CHAIN MEMPOOL → SENTIMENT → BRAIN MEMORY → HUMAN APPROVAL",
                                style={"color": C["muted"], "fontSize": "10px", "letterSpacing": "1px"},
                            ),
                            html.Div(id="research", style={"marginTop": "10px"}),
                        ],
                        style={"flex": "1"},
                        className="cyber-card",
                    ),
                    html.Div(
                        [
                            html.Div("▚ DATA INTERPRETATION & PROTECTION", className="cyber-header"),
                            html.Div(
                                [
                                    html.Div("• Exchange deposits ≠ immediate sell — can be custody move.", style={"fontSize": "11px", "color": C["muted"]}),
                                    html.Div("• Mempool tx ≥100 BTC ≠ exchange flow — no attribution, verify in explorer.", style={"fontSize": "11px", "color": C["muted"]}),
                                    html.Div("• Flip protection ACTIVE: contradictory 5m flips blocked to protect fund.", style={"fontSize": "11px", "color": C["green"]}),
                                    html.Div("• Portfolio Guardian enforces max risk, daily stop, mandatory SL.", style={"fontSize": "11px", "color": C["amber"]}),
                                ]
                            ),
                        ],
                        style={"flex": "1"},
                        className="cyber-card",
                    ),
                ],
                style={"display": "flex", "gap": "12px", "flexWrap": "wrap"},
            ),
            # Controls
            html.Div(
                [
                    dcc.Dropdown(
                        [{"label": f"[{tf.upper()}] CHART_FEED", "value": tf} for tf in ("1h", "4h", "1d")],
                        "1h",
                        id="timeframe",
                        clearable=False,
                        style={"width": "180px", "color": "black"},
                    ),
                    html.Button("▶ REFRESH_NOW", id="refresh", n_clicks=0),
                    html.Span(" SEGFAULT PROTECTION: ON | BRAINDUMP: ENABLED | PORTFOLIO_SHIELD: ACTIVE", style={"color": C["muted"], "fontSize": "10px", "marginLeft": "16px", "alignSelf": "center"}),
                ],
                style={"display": "flex", "gap": "10px", "margin": "12px 0", "alignItems": "center"},
            ),
            dcc.Graph(id="chart", config={"displayModeBar": False}, style={"background": C["card"], "borderRadius": "4px", "border": f"1px solid {C['border_dim']}", "marginBottom": "12px"}),
            # Signals + News + Queue
            html.Div(
                [
                    html.Div([html.Div("▚ SIGNAL MATRIX", className="cyber-header"), html.Div(id="signals")], style={"flex": "1"}, className="cyber-card"),
                    html.Div([html.Div("▚ NEWS / EVENT WATCH", className="cyber-header"), html.Div(id="news")], style={"flex": "1"}, className="cyber-card"),
                    html.Div(
                        [
                            html.Div("▚ HUMAN APPROVAL QUEUE", className="cyber-header"),
                            html.Div(id="queue"),
                            html.Div(
                                [
                                    html.Button("✔ APPROVE_LAST_PLAN", id="approve", n_clicks=0),
                                    html.Button("✖ REJECT_LAST_PLAN", id="reject", n_clicks=0, style={"marginLeft": "8px"}),
                                ],
                                style={"marginTop": "8px"},
                            ),
                            html.Div(id="action", style={"marginTop": "8px", "fontSize": "11px", "color": C["amber"]}),
                        ],
                        style={"flex": "1"},
                        className="cyber-card",
                    ),
                ],
                style={"display": "flex", "gap": "12px", "flexWrap": "wrap"},
            ),
            # CVD + Brain Memory
            html.Div(
                [
                    html.Div([html.Div("▚ ORDER FLOW (CVD)", className="cyber-header"), html.Div(id="cvd")], style={"flex": "1"}, className="cyber-card"),
                    html.Div([html.Div("▚ BRAIN MEMORY & TRADE JOURNAL", className="cyber-header"), html.Div(id="brainmemory")], style={"flex": "1.3"}, className="cyber-card"),
                ],
                style={"display": "flex", "gap": "12px", "flexWrap": "wrap"},
            ),
            # Polymarket
            html.Div([html.Div("▚ PREDICTION MARKETS (Polymarket)", className="cyber-header"), html.Div(id="polymarket")], className="cyber-card"),
            html.Div(
                "EDUCATIONAL DECISION SUPPORT ONLY // NO WALLET · NO TRADING KEY · NO AUTO-ORDERS // APPROVAL = LOCAL PAPER PLAN ONLY // PORTFOLIO > SIGNAL",
                style={"color": C["muted"], "fontSize": "10px", "textAlign": "center", "marginTop": "16px", "letterSpacing": "1px"},
            ),
        ],
        style={
            "background": "transparent",
            "color": C["text"],
            "minHeight": "100vh",
            "padding": "0 20px 20px 20px",
            "fontFamily": "'JetBrains Mono', monospace",
        },
    )

    @app.callback(
        Output("status", "children"),
        Output("overview", "children"),
        Output("macro", "children"),
        Output("decision", "children"),
        Output("derivatives", "children"),
        Output("onchain", "children"),
        Output("health", "children"),
        Output("research", "children"),
        Output("chart", "figure"),
        Output("signals", "children"),
        Output("news", "children"),
        Output("queue", "children"),
        Output("polymarket", "children"),
        Output("cvd", "children"),
        Output("tradequality", "children"),
        Output("stability", "children"),
        Output("portfolio", "children"),
        Output("brainmemory", "children"),
        Input("poll", "n_intervals"),
        Input("timeframe", "value"),
        Input("refresh", "n_clicks"),
    )
    def render(_, tf, __):
        service.refresh()
        d = service.snapshot()
        t = d.get("ticker", {})
        cycle = d.get("cycle", {})
        dec = d.get("decision")
        queued = d.get("queued", {})
        stability = d.get("stability", {})
        brain_status = d.get("brain_status", {})
        portfolio_status = d.get("portfolio_status", {})
        portfolio_exposure = d.get("portfolio_exposure", {})
        portfolio_open = d.get("portfolio_open", [])
        brain_memory_list = d.get("brain_memory", [])

        # Status
        status_txt = f"{d.get('status')} • {d.get('at','waiting')} • MEMPOOL_SYNC ✓ • BRAIN_MEM {len(brain_memory_list)} REC"

        # Overview — hero price with mempool style
        price = t.get("price", 0)
        chg = t.get("change_pct", 0)
        price_color = C["green"] if chg >= 0 else C["red"]
        cycle_score = cycle.get("score", "—")
        cycle_phase = cycle.get("phase", "—")
        overview = html.Div([
            html.Div("▚ PRICE FEED [BTC/USDT]", className="cyber-header", style={"fontSize": "11px"}),
            html.Div([
                html.Span(f"${price:,.0f}", style={"color": C["gold"], "fontSize": "32px", "fontWeight": "800", "textShadow": f"0 0 20px {C['gold']}88"}),
                html.Span(" BTC", style={"color": C["muted"], "fontSize": "14px"}),
            ]),
            html.Div([
                html.Span(f"{chg:+.2f}%", style={"color": price_color, "background": f"{price_color}22", "padding": "2px 8px", "borderRadius": "3px", "fontWeight": "700"}),
                html.Span(f" 24H • CYCLE {cycle_score}/10 {cycle_phase}", style={"color": C["muted"], "fontSize": "11px", "marginLeft": "8px"}),
            ], style={"marginTop": "6px"}),
            html.Div([
                html.Div(f"MEMPOOL: {d.get('onchain',{}).get('mempool_tx_count','—')} tx | {d.get('onchain',{}).get('mempool_vsize_mb','—')} MB", style={"fontSize": "11px", "color": C["cyan"], "marginTop": "10px"}),
                html.Div(f"FEES: fast {d.get('onchain',{}).get('fees_recommended',{}).get('fastestFee','—')} sat/vB | low {d.get('onchain',{}).get('fees_recommended',{}).get('minimumFee','—')}", style={"fontSize": "10px", "color": C["muted"]}),
            ]),
        ])

        # Macro
        macro = d.get("macro", {})
        markets = macro.get("markets", {})
        macro_bias = macro.get("macro_bias", "Unavailable")
        bias_color = C["green"] if "BULLISH" in macro_bias else C["red"] if "BEARISH" in macro_bias else C["amber"]
        macro_rows = []
        for name in ("SPX", "NASDAQ", "US30", "FTSE100", "DAX", "NIKKEI", "DXY", "GOLD", "OIL"):
            x = markets.get(name)
            if x and x.get("available"):
                col = C["green"] if x.get("change", 0) >= 0 else C["red"]
                macro_rows.append(html.Div([
                    html.Span(f"{name:8s}", style={"color": C["muted"], "fontSize": "11px", "display": "inline-block", "width": "70px"}),
                    html.Span(f"{x.get('change',0):+6.2f}%", style={"color": col, "fontSize": "11px", "fontWeight": "600"}),
                    html.Span(f" ${x.get('price',0):,.0f}", style={"color": C["text"], "fontSize": "10px", "marginLeft": "6px"}),
                ], style={"display": "flex"}))
        macro_ui = html.Div([
            html.Div("▚ MACRO FEED", className="cyber-header", style={"fontSize": "11px"}),
            html.Div(f"BIAS: {macro_bias}", style={"color": bias_color, "fontWeight": "700", "fontSize": "12px", "marginBottom": "6px"}),
            html.Div(macro_rows),
            html.Div(f"VIX: {markets.get('VIX',{}).get('price','—')} | FED: {macro.get('fed',{}).get('fed_funds_rate','—')}", style={"fontSize": "10px", "color": C["muted"], "marginTop": "6px"}),
        ])

        # Decision — strategy core with portfolio shield
        if dec:
            color = C["green"] if "LONG" in dec.action else C["red"] if "SHORT" in dec.action else C["amber"]
            is_suppressed = queued.get("status") == "SUPPRESSED"
            if is_suppressed:
                decision = html.Div([
                    html.Div("▚ STRATEGY CORE [SUPPRESSED]", className="cyber-header", style={"fontSize": "11px"}),
                    html.Div([
                        html.Span("⚠ FLIP BLOCKED ", style={"color": C["amber"], "fontWeight": "800"}),
                        html.Span(f"Proposed: {queued.get('proposed_action')} @ ${queued.get('proposed_price',0):,.0f}", style={"color": C["muted"], "fontSize": "11px"}),
                    ]),
                    html.Div(queued.get("reason",""), style={"color": C["amber"], "fontSize": "11px", "background": "rgba(255,170,0,0.1)", "padding": "8px", "borderLeft": f"2px solid {C['amber']}", "marginTop": "6px", "whiteSpace": "pre-wrap"}),
                    html.Div(f"KEPT: {queued.get('kept_action',{}).get('action','—')} @ ${queued.get('kept_action',{}).get('entry_price',0):,.0f}" if isinstance(queued.get('kept_action'), dict) else "", style={"color": C["green"], "fontSize": "12px", "marginTop": "6px"}),
                    html.Div(f"${dec.entry_price:,.0f} → SL ${dec.stop_loss:,.0f} | TP1 ${dec.take_profit_1:,.0f} | RR {dec.risk_reward:.2f} | CONF {dec.confidence:.0f}%", style={"fontSize": "11px", "color": C["muted"], "marginTop": "8px"}),
                ])
            else:
                decision = html.Div([
                    html.Div("▚ STRATEGY CORE", className="cyber-header", style={"fontSize": "11px"}),
                    html.Div([
                        html.Span(f" {dec.action}", style={"color": color, "fontSize": "20px", "fontWeight": "800", "textShadow": f"0 0 12px {color}"}),
                        html.Span(f" • {dec.confidence:.0f}%", style={"color": C["text_bright"], "fontSize": "14px", "marginLeft": "8px"}),
                        html.Span(f" • RR {dec.risk_reward:.2f}", style={"color": C["cyan"], "fontSize": "11px", "marginLeft": "6px"}),
                    ]),
                    html.Div(f"ENTRY ${dec.entry_price:,.0f} | SL ${dec.stop_loss:,.0f} | TP1 ${dec.take_profit_1:,.0f} TP2 ${dec.take_profit_2:,.0f} TP3 ${dec.take_profit_3:,.0f}", style={"fontSize": "11px", "color": C["text"], "marginTop": "6px", "lineHeight": "1.6"}),
                    html.Div(f"LEV {dec.leverage}x | SIZE {dec.position_size}% risk | {dec.time_horizon} | MARGIN ~${dec.position_size*dec.entry_price/100*10:,.0f}", style={"fontSize": "10px", "color": C["muted"]}),
                    html.Div(" • ".join(dec.reasoning[:4]), style={"fontSize": "11px", "color": C["muted"], "marginTop": "6px", "whiteSpace": "pre-wrap"}),
                    html.Div(f"INV: {dec.invalidation}", style={"fontSize": "11px", "color": C["red"], "marginTop": "4px", "background": "rgba(255,51,85,0.08)", "padding": "3px 6px"}),
                ])
        else:
            decision = html.Div([html.Div("▚ STRATEGY CORE", className="cyber-header"), html.Div("Analyzing... ▌", className="blink", style={"color": C["green"]})])

        # Stability
        stab_stat = brain_status.get("status", "UNKNOWN")
        if stab_stat == "COOLDOWN_ACTIVE":
            stab_col = C["amber"]
            stab_icon = "◷"
        elif stab_stat == "READY":
            stab_col = C["green"]
            stab_icon = "✔"
        else:
            stab_col = C["muted"]
            stab_icon = "○"
        stability_ui = html.Div([
            html.Div("▚ SIGNAL STABILITY [ANTI-WHIPSAW]", className="cyber-header", style={"fontSize": "11px"}),
            html.Div([
                html.Span(f"{stab_icon} {stab_stat}", style={"color": stab_col, "fontWeight": "700", "fontSize": "12px"}),
                html.Span(f" • COOLDOWN {SIGNAL_COOLDOWN_MINUTES}m | FLIP_THRESH {FLIP_PRICE_THRESHOLD_PCT}% | MIN_CONF {MIN_CONF_TO_FLIP}%", style={"color": C["muted"], "fontSize": "9px", "marginLeft": "8px"}),
            ]),
            html.Div(brain_status.get("message","Awaiting first signal..."), style={"color": C["text"], "fontSize": "11px", "marginTop": "8px", "background": f"{stab_col}11", "borderLeft": f"2px solid {stab_col}", "padding": "6px", "whiteSpace": "pre-wrap"}),
            html.Div(f"Last: {brain_status.get('last_action','—')} @ {brain_status.get('last_price',0):,.0f} {brain_status.get('last_timestamp','')}", style={"fontSize": "10px", "color": C["muted"], "marginTop": "4px"}),
            html.Div(f"OpenShield: {len(portfolio_open)} trades active — opposite flip blocked unless +{FLIP_PRICE_THRESHOLD_PCT}% move + {MIN_CONF_TO_FLIP}% conf", style={"fontSize": "10px", "color": C["amber"], "marginTop": "4px"}),
        ])

        # Chart
        df = d.get("frames", {}).get(tf, pd.DataFrame())
        fig = go.Figure()
        if not df.empty:
            fig.add_trace(go.Candlestick(x=df.index, open=df.open, high=df.high, low=df.low, close=df.close, name="BTC", increasing_line_color=C["green"], decreasing_line_color=C["red"], increasing_fillcolor=C["green"], decreasing_fillcolor=C["red"]))
            for col, color in (("ema_21", C["amber"]), ("ema_50", C["green"]), ("ema_200", C["cyan"])):
                if col in df:
                    fig.add_trace(go.Scatter(x=df.index, y=df[col], name=col.upper(), line={"color": color, "width": 1}))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor=C["card"],
            plot_bgcolor=C["card"],
            height=520,
            xaxis_rangeslider_visible=False,
            margin={"l": 20, "r": 10, "t": 10, "b": 20},
            font={"family": "JetBrains Mono", "color": C["text"], "size": 10},
            legend={"orientation": "h", "y": 1.02, "x": 0, "font": {"size": 10}},
        )
        fig.update_xaxes(gridcolor="rgba(0,255,65,0.06)", showgrid=True)
        fig.update_yaxes(gridcolor="rgba(0,255,65,0.06)", showgrid=True, tickprefix="$")

        # Signals
        sigs = d.get("signals", [])
        sigrows = [{"Signal": s.name, "Read": s.current_read, "Status": s.status} for s in sigs]
        def sig_color(status):
            if status == "GREEN":
                return C["green"]
            if status == "RED":
                return C["red"]
            return C["amber"]
        sigui = html.Div([
            dash_table.DataTable(
                sigrows,
                [{"name": x, "id": x} for x in ("Signal", "Read", "Status")],
                style_header={"backgroundColor": C["card2"], "color": C["cyan"], "border": f"1px solid {C['border_dim']}", "fontSize": "10px", "textTransform": "uppercase"},
                style_data={"backgroundColor": C["card"], "color": C["text"], "border": f"1px solid {C['border_dim']}22", "fontSize": "11px"},
                style_data_conditional=[{"if": {"column_id": "Status", "filter_query": "{Status} = GREEN"}, "color": C["green"], "fontWeight": "700"}, {"if": {"column_id": "Status", "filter_query": "{Status} = RED"}, "color": C["red"], "fontWeight": "700"}, {"if": {"column_id": "Status", "filter_query": "{Status} = YELLOW"}, "color": C["amber"]}],
            )
        ])

        # News
        news_data = d.get("news", {})
        all_news = news_data.get("all_news", [])[:10]
        if all_news:
            news_ui = html.Div([
                html.Div([
                    html.Span(f"[{getattr(n,'category','GEN')}/{getattr(n,'impact','LOW')}]", style={"color": C["amber"] if getattr(n, 'impact', '') == 'HIGH' else C["muted"], "fontSize": "10px"}),
                    html.Span(f" {n.title[:85]}", style={"color": C["text"], "fontSize": "11px"}),
                ], style={"marginBottom": "4px", "borderLeft": f"2px solid {C['border_dim']}", "paddingLeft": "6px"})
                for n in all_news
            ])
        else:
            news_ui = html.Div("No RSS feed — retaining last snapshot // check source URLs below", style={"color": C["muted"], "fontSize": "11px"})

        # Queue
        pending = service.decisions.get_pending_summary()
        if pending:
            q_rows = []
            for p in pending[-5:]:
                warn = p.get("risk_warning")
                q_rows.append(html.Div([
                    html.Span(f"#{p['id']}", style={"color": C["cyan"], "fontSize": "10px"}),
                    html.Span(f" {p['action']} @ ${p['entry_price']:,.0f}", style={"color": C["text_bright"] if "LONG" in p['action'] else C["red"] if "SHORT" in p['action'] else C["amber"], "fontWeight": "700", "fontSize": "12px"}),
                    html.Span(f" CONF {p['confidence']:.0f}% RR {p['risk_reward']:.2f} LEV {p['leverage']}x MARGIN ~${p.get('margin_usd',0):,.0f}", style={"color": C["muted"], "fontSize": "10px", "marginLeft": "6px"}),
                    html.Div(warn, style={"color": C["red"], "fontSize": "10px", "background": "rgba(255,51,85,0.1)", "padding": "2px 4px", "marginTop": "2px"}) if warn else html.Div(),
                ], style={"borderLeft": f"2px solid {C['green_dim']}", "paddingLeft": "6px", "marginBottom": "6px"}))
            queue_ui = html.Div(q_rows)
        else:
            if queued.get("status") == "SUPPRESSED":
                queue_ui = html.Div([
                    html.Div("🛡️ FLIP PROTECTION: No new plan — keeping previous thesis to avoid whipsaw.", style={"color": C["amber"], "fontSize": "11px", "background": "rgba(255,170,0,0.1)", "padding": "6px", "borderLeft": f"2px solid {C['amber']}"}),
                    html.Div(queued.get("reason",""), style={"color": C["muted"], "fontSize": "10px", "marginTop": "4px"}),
                ])
            else:
                queue_ui = html.Div("No high-conviction plan awaiting approval // brain reports STAND_ASIDE safer", style={"color": C["muted"], "fontSize": "11px"})

        # Derivatives
        fund = d.get("funding", {})
        oi_data = d.get("oi", {})
        ls_data = d.get("ls", {})
        book = d.get("book", {})
        derivatives_ui = html.Div([
            html.Div("▚ DERIVATIVES POSITIONING", className="cyber-header", style={"fontSize": "11px"}),
            html.Div(f"Funding {fund.get('current',0):+.4f}% • {fund.get('status','Unknown')}", style={"fontSize": "11px", "color": C["text"]}),
            html.Div(f"Open interest {oi_data.get('change_24h',0):+.2f}% • {oi_data.get('status','Unknown')}", style={"fontSize": "11px", "color": C["text"]}),
            html.Div(f"Long/short {ls_data.get('long_short_ratio',1):.2f} • {ls_data.get('status','Unknown')}", style={"fontSize": "11px", "color": C["text"]}),
            html.Div(f"Order book {book.get('imbalance',0):+.2f}% • {book.get('status','Unknown')}", style={"fontSize": "11px", "color": C["muted"]}),
        ])

        # On-chain — enhanced mempool.space visual
        chain = d.get("onchain", {})
        mempool_blocks = chain.get("mempool_blocks", [])
        fees_rec = chain.get("fees_recommended", {})
        diff_adj = chain.get("difficulty_adjustment", {})
        large_tx = chain.get("large_mempool_tx", [])
        recent_blocks = chain.get("recent_blocks", [])

        # Build mempool block visual like mempool.space
        block_visual = []
        for i, b in enumerate(mempool_blocks[:6]):
            median = b.get("medianFee", 0)
            if median >= 20:
                cls = "fee-high"
            elif median >= 8:
                cls = "fee-mid"
            else:
                cls = "fee-low"
            block_visual.append(
                html.Div([
                    html.Div(f"{median:.0f}", style={"fontSize": "10px", "lineHeight": "12px", "marginTop": "4px"}),
                    html.Div(f"{b.get('nTx',0)}tx", style={"fontSize": "8px", "opacity": "0.7"}),
                ], className=f"mempool-block {cls}", title=f"{b.get('nTx')} tx, median {median} sat/vB, {b.get('blockVSize')} MB")
            )

        onchain_ui = html.Div([
            html.Div("▚ ON-CHAIN & MEMPOOL EXPLORER [mempool.space]", className="cyber-header", style={"fontSize": "11px"}),
            html.Div([
                html.Span(f"Block {chain.get('block_height','—')}", style={"color": C["cyan"], "fontSize": "11px"}),
                html.Span(f" • Mempool {chain.get('mempool_tx_count','—')} tx", style={"color": C["text"], "fontSize": "11px", "marginLeft": "8px"}),
                html.Span(f" • {chain.get('mempool_vsize_mb','—')} MB", style={"color": C["muted"], "fontSize": "11px", "marginLeft": "4px"}),
                html.Span(f" • Fees {chain.get('mempool_fees_btc','—')} BTC", style={"color": C["amber"], "fontSize": "10px", "marginLeft": "8px"}),
            ]),
            html.Div(block_visual, style={"marginTop": "8px", "display": "flex", "flexWrap": "wrap"}),
            html.Div([
                html.Div(f"FAST {fees_rec.get('fastestFee','—')} | 30M {fees_rec.get('halfHourFee','—')} | 1H {fees_rec.get('hourFee','—')} | MIN {fees_rec.get('minimumFee','—')} sat/vB", style={"fontSize": "10px", "color": C["muted"], "marginTop": "6px"}),
                html.Div(f"DIFF ADJ {diff_adj.get('progressPercent','—')}% | Δ {diff_adj.get('difficultyChange','—'):+}% | {diff_adj.get('remainingBlocks','—')} blocks to retarget", style={"fontSize": "10px", "color": C["cyan"], "marginTop": "2px"}),
            ]),
            html.Div([
                html.Div(f"Large pending ≥100 BTC: {len(large_tx)}", style={"fontSize": "11px", "color": C["amber"], "marginTop": "6px"}),
                html.Div([html.Div([
                    html.A(f"{x['btc']} BTC {x['txid'][:10]}…", href=f"https://mempool.space/tx/{x['txid']}", target="_blank", style={"color": C["green"], "fontSize": "10px"}),
                    html.Span(f" {x.get('fee_sat_vb',0)} sat/vB", style={"color": C["muted"], "fontSize": "10px", "marginLeft": "6px"}),
                ]) for x in large_tx[:3]], style={"marginTop": "2px"}),
            ]),
            html.Div("Mempool watch only — no sender/receiver attribution — verify in explorer →", style={"color": C["muted"], "fontSize": "9px", "marginTop": "6px"}),
        ])

        # Health
        health = d.get("exchange_status", {})
        health_color = C["green"] if health.get("available") else C["red"]
        health_ui = html.Div([
            html.Div("▚ EXCHANGE STATUS", className="cyber-header", style={"fontSize": "11px"}),
            html.Div([html.Span("●", style={"color": health_color}), html.Span(f" {health.get('message','Unknown')}", style={"color": health_color, "fontWeight": "700", "fontSize": "12px"})]),
            html.Div(f"Ping {health.get('ping_ms','—')} ms • {health.get('symbols_loaded','—')} symbols", style={"fontSize": "11px", "color": C["muted"]}),
            html.Div("No wallet, no API key, no auto orders", style={"color": C["muted"], "fontSize": "9px", "marginTop": "4px"}),
        ])

        # Research links
        links = d.get("research_links", {})
        research_ui = []
        for group, items in links.items():
            research_ui.append(html.Div([
                html.Span(f"{group.replace('_',' ').upper()}: ", style={"color": C["cyan"], "fontSize": "10px", "fontWeight": "700"}),
                *[html.A(x["name"], href=x["url"], target="_blank", style={"color": C["green"], "marginRight": "10px", "fontSize": "10px", "textDecoration": "none", "borderBottom": f"1px dashed {C['border_dim']}"}) for x in items],
            ], style={"marginBottom": "4px"}))

        # Polymarket
        poly = d.get("polymarket", {})
        poly_markets = poly.get("markets", [])
        poly_head = html.Div([
            html.Span(f"Bias: {poly.get('overall_bias','—')}", style={"color": C["green"] if poly.get("overall_bias")=="BULLISH" else C["red"] if poly.get("overall_bias")=="BEARISH" else C["amber"], "fontWeight": "700", "fontSize": "11px"}),
            html.Span(f" • net {poly.get('net_shift_pts',0):+.1f}pt", style={"color": C["muted"], "fontSize": "11px"}),
            html.Span(f" • {poly.get('correlation_note','')}", style={"color": C["muted"], "fontSize": "10px", "marginLeft": "8px"}),
        ], style={"marginBottom": "6px"})
        if poly_markets:
            rows = [{"Market": m["question"][:60], "Type": m["category"], "P(Yes)": f"{m['yes_price']:.0f}%", "Δ 1d": (f"{m['change_1d']:+.1f}pt" if m.get("change_1d") is not None else "—")} for m in poly_markets[:10]]
            poly_table = dash_table.DataTable(
                rows,
                [{"name": c, "id": c} for c in ("Market", "Type", "P(Yes)", "Δ 1d")],
                style_header={"backgroundColor": C["card2"], "color": C["cyan"], "fontSize": "10px"},
                style_data={"backgroundColor": C["card"], "color": C["text"], "fontSize": "11px"},
                style_data_conditional=[{"if": {"column_id": "Δ 1d"}, "color": C["green"]}],
            )
            polymarket_ui = html.Div([poly_head, poly_table])
        else:
            polymarket_ui = html.Div([poly_head, html.Div("No Polymarket markets yet — retaining snapshot", style={"color": C["muted"], "fontSize": "11px"})])

        # CVD
        cvd = d.get("cvd", {})
        cvd_bull = cvd.get("status", "Unknown") in ("Strong Buying", "Buying")
        cvd_color = C["green"] if cvd_bull else C["red"] if cvd.get("status", "") in ("Strong Selling", "Selling") else C["amber"]
        cvd_ui = html.Div([
            html.Div([html.Span(f"{cvd.get('status','Unknown')}", style={"color": cvd_color, "fontWeight": "800"}), html.Span(f" • Δ {cvd.get('net_delta',0):,.0f}", style={"color": C["muted"], "fontSize": "11px", "marginLeft": "6px"})]),
            html.Div(f"Buy {cvd.get('buy_volume',0):,.0f} vs Sell {cvd.get('sell_volume',0):,.0f}", style={"fontSize": "11px"}),
            html.Div(f"Buy pressure {cvd.get('buy_pressure',0):.1f}% • {cvd.get('n_trades',0)} trades", style={"fontSize": "11px", "color": C["muted"]}),
            html.Div(("⚠ Divergence: price ↑ delta ↓ or vice versa — absorption warning" if cvd.get("divergence") else "No divergence"), style={"fontSize": "11px", "color": C["red"] if cvd.get("divergence") else C["muted"]}),
        ])

        # Trade quality
        tq = dec.trade_quality if dec and getattr(dec, "trade_quality", None) else None
        if tq:
            tq_color = C["green"] if tq["total"] >= 60 else C["red"] if tq["total"] < 45 else C["amber"]
            rows = [{"Factor": f["name"], "Score": f"{f['score']:.0f}/10", "Reason": f["reason"][:70]} for f in tq["factors"]]
            tq_table = dash_table.DataTable(
                rows,
                [{"name": x, "id": x} for x in ("Factor", "Score", "Reason")],
                style_header={"backgroundColor": C["card2"], "color": C["cyan"], "fontSize": "10px"},
                style_data={"backgroundColor": C["card"], "color": C["text"], "fontSize": "11px"},
            )
            tq_ui = html.Div([
                html.Div([html.Span(f"TRADE QUALITY {tq['total']:.0f}/100", style={"color": tq_color, "fontWeight": "800", "fontSize": "16px", "textShadow": f"0 0 10px {tq_color}"}), html.Span(f" — {tq['label']}", style={"color": C["muted"], "fontSize": "12px"})]),
                tq_table,
            ])
        else:
            tq_ui = html.Div("Quality scoring pending...", style={"color": C["muted"], "fontSize": "11px"})

        # Portfolio Guardian
        prot_status = portfolio_status.get("status", "UNKNOWN")
        if prot_status == "SAFE":
            prot_color = C["green"]
            prot_icon = "🛡️"
        elif prot_status == "WARNING":
            prot_color = C["amber"]
            prot_icon = "⚠️"
        elif prot_status in ("BLOCKED", "KILL_SWITCH"):
            prot_color = C["red"]
            prot_icon = "⛔"
        else:
            prot_color = C["muted"]
            prot_icon = "○"
        exp = portfolio_exposure
        portfolio_ui = html.Div([
            html.Div("▚ PORTFOLIO GUARDIAN [FUND PROTECTION]", className="cyber-header", style={"fontSize": "11px"}),
            html.Div([
                html.Span(f"{prot_icon} {prot_status}", style={"color": prot_color, "fontWeight": "800", "fontSize": "12px"}),
                html.Span(f" • {exp.get('open_count',0)}/{MAX_OPEN_TRADES} open • {exp.get('total_risk',0):.2f}%/{MAX_TOTAL_RISK_PERCENT}% risk", style={"color": C["text"], "fontSize": "11px", "marginLeft": "8px"}),
                html.Span(f" • Daily {exp.get('daily_pnl',0):+.2f}% (stop -{DAILY_LOSS_LIMIT_PCT}%)", style={"color": C["green"] if exp.get('daily_pnl',0)>=0 else C["red"], "fontSize": "11px", "marginLeft": "6px"}),
            ]),
            html.Div([html.Div(f"• {r}", style={"fontSize": "11px", "color": prot_color if prot_status!='SAFE' else C["muted"]}) for r in portfolio_status.get("reasons", [])[:3]], style={"marginTop": "6px"}),
            html.Div([
                html.Div([
                    html.Span(f"#{t.get('id')} {t.get('action')} @ ${t.get('entry_price',0):,.0f}", style={"color": C["green"] if "LONG" in str(t.get('action')) else C["red"], "fontSize": "11px", "fontWeight": "700"}),
                    html.Span(f" SL ${t.get('stop_loss',0):,.0f} TP ${t.get('take_profit_1',0):,.0f} LEV {t.get('leverage')}x {t.get('position_size')}% RR {t.get('risk_reward')}", style={"color": C["muted"], "fontSize": "10px", "marginLeft": "6px"}),
                    html.Span(f" MARGIN ~${t.get('margin_usd',0):,.0f}", style={"color": C["cyan"], "fontSize": "10px", "marginLeft": "6px"}),
                ], style={"borderLeft": f"2px solid {prot_color}", "paddingLeft": "6px", "marginBottom": "4px"})
                for t in portfolio_open[-3:]
            ], style={"marginTop": "8px"}) if portfolio_open else html.Div("No open positions — fund fully protected", style={"color": C["muted"], "fontSize": "11px", "marginTop": "6px"}),
        ])

        # Brain Memory — trade journal with leverage, margin, RR
        if brain_memory_list:
            mem_rows = []
            for m in brain_memory_list[:12]:
                # Parse reasoning json if needed
                action = m.get("action", "—")
                conf = m.get("confidence", 0)
                entry = m.get("entry_price", 0)
                lev = m.get("leverage", 1)
                rr = m.get("risk_reward", 0)
                ts = str(m.get("timestamp", ""))[:19]
                status = m.get("status", "PROPOSED")
                color_a = C["green"] if "LONG" in action else C["red"] if "SHORT" in action else C["amber"]
                mem_rows.append(html.Div([
                    html.Span(f"{ts}", style={"color": C["muted"], "fontSize": "9px", "display": "inline-block", "width": "130px"}),
                    html.Span(f"{action}", style={"color": color_a, "fontWeight": "700", "fontSize": "11px", "display": "inline-block", "width": "110px"}),
                    html.Span(f"${entry:,.0f}", style={"color": C["text"], "fontSize": "11px", "display": "inline-block", "width": "75px"}),
                    html.Span(f"{conf:.0f}%", style={"color": C["cyan"], "fontSize": "10px", "display": "inline-block", "width": "35px"}),
                    html.Span(f"{rr:.2f}R", style={"color": C["amber"], "fontSize": "10px", "display": "inline-block", "width": "40px"}),
                    html.Span(f"{lev}x", style={"color": C["text"], "fontSize": "10px", "display": "inline-block", "width": "25px"}),
                    html.Span(f"[{status}]", style={"color": C["green"] if status=="APPROVED" else C["amber"] if status=="PROPOSED" else C["muted"], "fontSize": "9px"}),
                ], style={"borderLeft": f"2px solid {color_a}44", "paddingLeft": "6px", "marginBottom": "3px", "background": f"{color_a}08"}))
            brainmemory_ui = html.Div([
                html.Div(f"BRAIN MEMORY: {len(brain_memory_list)} signals tracked • ANTI-WHIPSAW {SIGNAL_COOLDOWN_MINUTES}m | {FLIP_PRICE_THRESHOLD_PCT}% | {MIN_CONF_TO_FLIP}% conf", style={"color": C["cyan"], "fontSize": "10px", "marginBottom": "8px", "letterSpacing": "1px"}),
                html.Div(mem_rows),
                html.Div("Each entry: timestamp | action | entry | conf | RR | leverage | margin | status — full audit trail like blockchain explorer", style={"color": C["muted"], "fontSize": "9px", "marginTop": "8px"}),
            ])
        else:
            brainmemory_ui = html.Div("Brain memory initializing... will log every signal with leverage/margin/RR for finetuning", style={"color": C["muted"], "fontSize": "11px"})

        return status_txt, overview, macro_ui, decision, derivatives_ui, onchain_ui, health_ui, research_ui, fig, sigui, news_ui, queue_ui, polymarket_ui, cvd_ui, tq_ui, stability_ui, portfolio_ui, brainmemory_ui

    @app.callback(Output("action", "children"), Input("approve", "n_clicks"), Input("reject", "n_clicks"), prevent_initial_call=True)
    def action_cb(yes, no):
        from dash import ctx
        pending = service.decisions.get_pending_summary()
        if not pending:
            return html.Span("Nothing to approve.", style={"color": C["muted"]})
        plan = pending[-1]
        if ctx.triggered_id == "approve":
            res = service.decisions.approve_trade(plan["id"])
            if "error" in res:
                return html.Span(f"⛔ BLOCKED: {res['error']}", style={"color": C["red"]})
            return html.Span(f"✔ Approved #{plan['id']} {plan['action']} @ ${plan['entry_price']:,.0f} LEV {plan['leverage']}x RR {plan['risk_reward']} — recorded to brain memory & portfolio guardian. No order sent.", style={"color": C["green"]})
        service.decisions.reject_trade(plan["id"], "Manual rejection")
        return html.Span(f"✖ Rejected #{plan['id']}. Logged to brain memory for learning.", style={"color": C["amber"]})

    return app
