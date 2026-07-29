"""Human-in-the-loop Dash interface.

Approvals and closes affect a local SQLite-backed paper portfolio only.  This
module has no exchange-authentication or order-execution path.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import logging
import threading

import dash
from dash import Dash, dcc, html, Input, Output, State, dash_table
import pandas as pd
import plotly.graph_objects as go

from config import REFRESH_SECONDS
from data.indicators import TechnicalIndicators

logger = logging.getLogger(__name__)
C = {"bg": "#0b1020", "card": "#151c30", "text": "#e7edf8", "green": "#38d996",
     "red": "#ff6b6b", "gold": "#ffd166", "muted": "#93a4c7"}


class BrainService:
    def __init__(self, fetcher, db, news, macro, brain, decisions, cycle, signals, mtf, backtester):
        self.fetcher, self.db, self.news, self.macro = fetcher, db, news, macro
        self.brain, self.decisions, self.cycle, self.signals, self.mtf, self.backtester = brain, decisions, cycle, signals, mtf, backtester
        self.lock, self.refresh_lock, self.cache = threading.Lock(), threading.Lock(), {"status": "Starting…"}
        self._news_at = self._macro_at = 0

    def refresh(self):
        # Browser callbacks and the monitor thread share one provider pipeline.
        if not self.refresh_lock.acquire(blocking=False):
            return
        try:
            with ThreadPoolExecutor(max_workers=7) as pool:
                fs = {tf: pool.submit(self.fetcher.get_ohlcv, tf, 500 if tf != "1d" else 730) for tf in ("1h", "4h", "1d")}
                ticker = pool.submit(self.fetcher.get_24h_ticker)
                funding = pool.submit(self.fetcher.get_funding_rate)
                oi = pool.submit(self.fetcher.get_open_interest)
                liquidations = pool.submit(self.fetcher.get_liquidations)
                fear_greed = pool.submit(self.fetcher.get_fear_greed)
                book = pool.submit(self.fetcher.get_order_book_imbalance)
                frames = {tf: TechnicalIndicators.add_all_indicators(future.result()) for tf, future in fs.items()}
                ticker, funding, oi = ticker.result(), funding.result(), oi.result()
                liquidations, fear_greed, book = liquidations.result(), fear_greed.result(), book.result()

            # Offline/provider fallback: analyse retained local candles, but never invent a live price.
            for tf, frame in list(frames.items()):
                if frame.empty:
                    frames[tf] = TechnicalIndicators.add_all_indicators(self.db.load_candles(tf))
            if (frames["1h"].empty or frames["4h"].empty or frames["1d"].empty or
                    float(ticker.get("price", 0)) <= 0):
                raise RuntimeError("primary BTC market data unavailable; retaining last successful snapshot")

            now = datetime.now().timestamp()
            old = self.cache
            news = old.get("news", {}) if now - self._news_at < 300 else self.news.get_news_summary()
            macro = old.get("macro", {}) if now - self._macro_at < 300 else self.macro.get_macro_summary()
            if news is not old.get("news"):
                self._news_at = now
            if macro is not old.get("macro"):
                self._macro_at = now
            cycle = self.cycle.calculate_cycle_score(frames["1d"], funding, oi, fear_greed, liquidations)
            signal_list = self.signals.generate_all_signals(
                frames["1h"], funding, oi, fear_greed, liquidations, book, {"volume_change_pct": 0}
            )
            mtf = self.mtf.analyze_all_timeframes(self.fetcher)
            decision = self.brain.make_decision(
                {"price": ticker.get("price", 0), "funding": funding},
                signal_list, mtf, news, macro, cycle, frames["1h"],
            )
            queued = self.decisions.submit(decision)
            for tf, frame in frames.items():
                self.db.save_candles(tf, frame)
            if news.get("all_news"):
                self.db.save_news(news["all_news"])
            # DecisionManager owns the durable paper-plan row and all later state
            # transitions, so dashboard and database ids can never diverge.
            with self.lock:
                self.cache = {
                    "status": "Live", "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "frames": frames, "ticker": ticker, "funding": funding, "oi": oi,
                    "fg": fear_greed, "ls": liquidations, "book": book, "news": news,
                    "macro": macro, "cycle": cycle, "signals": signal_list, "mtf": mtf,
                    "decision": decision, "queued": queued,
                }
        except Exception as exc:
            logger.exception("refresh failed")
            with self.lock:
                self.cache = {**self.cache, "status": f"Degraded: {exc}"}
        finally:
            self.refresh_lock.release()

    def snapshot(self):
        with self.lock:
            return self.cache.copy()


def _money(value):
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _decision_panel(decision):
    if not decision:
        return html.Div("Analyzing…")
    color = C["green"] if "LONG" in decision.action else C["red"] if "SHORT" in decision.action else C["gold"]
    reasoning = decision.reasoning if isinstance(decision.reasoning, list) else [str(decision.reasoning)]
    heading = html.H2(f"{decision.action} • {decision.confidence:.0f}%", style={"color": color})
    context = html.Div(" • ".join(reasoning[:4]), style={"fontSize": "12px", "color": C["muted"]})
    if decision.action == "STAND_ASIDE":
        return html.Div([
            heading,
            html.Div("No paper trade setup. Entry, stop, and take-profit levels are intentionally not generated."),
            context,
            html.Div("Wait condition: " + decision.invalidation, style={"fontSize": "12px", "color": C["gold"]}),
        ])
    levels = html.Div(
        f"Entry {_money(decision.entry_price)} | Stop {_money(decision.stop_loss)} | "
        f"TP1 {_money(decision.take_profit_1)} | R:R {decision.risk_reward:.2f}"
    )
    return html.Div([
        heading, levels, context,
        html.Div(f"Invalidation: {decision.invalidation}", style={"fontSize": "12px", "color": C["red"]}),
    ])


def _portfolio_panel(decisions, current_price):
    open_trades = decisions.get_open_trades_summary(current_price)
    history = decisions.get_trade_history()
    stats = decisions.get_performance_stats()
    stats_ui = html.Div([
        html.Span(f"Closed {stats['total']}", style={"marginRight": "18px"}),
        html.Span(f"Wins {stats['wins']}", style={"color": C["green"], "marginRight": "18px"}),
        html.Span(f"Losses {stats['losses']}", style={"color": C["red"], "marginRight": "18px"}),
        html.Span(f"Win rate {stats['win_rate']:.1f}%", style={"marginRight": "18px"}),
        html.Span(f"Realized {stats['total_pnl']:+.2f}%", style={"color": C["green"] if stats["total_pnl"] >= 0 else C["red"]}),
    ], style={"marginBottom": "10px"})

    if open_trades:
        open_ui = html.Div([
            html.Div([
                html.B(f"#{trade['id']} {trade['action']}"),
                html.Span(
                    "  Entry " + _money(trade.get("entry_price")) +
                    " • Live " + _money(trade.get("current_price")) +
                    (f" • P&L {trade['unrealized_pnl_pct']:+.2f}%" if trade.get("unrealized_pnl_pct") is not None else " • P&L unavailable"),
                    style={"color": C["green"] if (trade.get("unrealized_pnl_pct") or 0) >= 0 else C["red"]},
                ),
                html.Div(
                    f"Stop {_money(trade.get('stop_loss'))} • TP1 {_money(trade.get('take_profit_1'))} "
                    "• directional BTC move before fees/funding",
                    style={"fontSize": "11px", "color": C["muted"]},
                ),
            ], style={"padding": "7px 0", "borderBottom": "1px solid #29334e"})
            for trade in open_trades
        ])
    else:
        open_ui = html.Div("No approved paper trades are open.", style={"color": C["muted"]})

    rows = [{
        "ID": trade["id"], "Plan": trade["action"], "Entry": _money(trade.get("entry_price")),
        "Exit": _money(trade.get("exit_price")), "Result": trade["status"],
        "P&L": f"{trade.get('pnl_pct', 0):+.2f}%", "Closed": trade.get("closed_at") or "—",
    } for trade in history[:20]]
    history_ui = dash_table.DataTable(
        rows,
        [{"name": column, "id": column} for column in ("ID", "Plan", "Entry", "Exit", "Result", "P&L", "Closed")],
        page_size=8,
        style_header={"backgroundColor": "#202943", "color": "white", "fontWeight": "bold"},
        style_data={"backgroundColor": C["card"], "color": "white"},
        style_cell={"border": "1px solid #29334e", "padding": "6px", "textAlign": "left"},
        style_data_conditional=[
            {"if": {"filter_query": "{Result} = WIN", "column_id": "Result"}, "color": C["green"]},
            {"if": {"filter_query": "{Result} = LOSS", "column_id": "Result"}, "color": C["red"]},
        ],
    ) if rows else html.Div("No closed paper-trade records yet.", style={"color": C["muted"], "marginTop": "10px"})
    return html.Div([stats_ui, html.H5("Open paper trades"), open_ui, html.H5("Win / loss record"), history_ui])


def create_app(service: BrainService) -> Dash:
    app = dash.Dash(__name__)
    app.title = "BTC AI Brain"
    card = {"background": C["card"], "padding": "14px", "borderRadius": "8px", "marginBottom": "10px"}
    app.layout = html.Div([
        dcc.Interval(id="poll", interval=REFRESH_SECONDS * 1000, n_intervals=0),
        dcc.Store(id="portfolio-version", data=0),
        html.H1("₿ BTC AI BRAIN", style={"color": C["gold"], "marginBottom": "0"}),
        html.Div(id="status", style={"color": C["muted"], "marginBottom": "12px"}),
        html.Div([
            html.Div(id="decision", style={**card, "flex": "2", "border": f"1px solid {C['gold']}"}),
            html.Div(id="overview", style={**card, "flex": "1"}),
            html.Div(id="macro", style={**card, "flex": "1"}),
        ], style={"display": "flex", "gap": "10px", "flexWrap": "wrap"}),
        html.Div([
            dcc.Dropdown([{"label": tf, "value": tf} for tf in ("1h", "4h", "1d")], "1h", id="timeframe", clearable=False, style={"width": "120px", "color": "black"}),
            html.Button("Refresh now", id="refresh", n_clicks=0),
            html.Span("Use the chart modebar to draw lines/shapes or erase them.", style={"color": C["muted"], "fontSize": "12px", "alignSelf": "center"}),
        ], style={"display": "flex", "gap": "10px"}),
        dcc.Graph(
            id="chart",
            config={
                "displayModeBar": True, "displaylogo": False, "scrollZoom": True,
                "modeBarButtonsToAdd": ["drawline", "drawopenpath", "drawrect", "drawcircle", "eraseshape"],
            },
            style=card,
        ),
        html.Div([
            html.Div([html.H4("Signals"), html.Div(id="signals")], style={**card, "flex": "1"}),
            html.Div([html.H4("News / event watch"), html.Div(id="news")], style={**card, "flex": "1"}),
            html.Div([
                html.H4("Human approval queue"), html.Div(id="queue"),
                html.Button("Approve latest paper plan", id="approve", n_clicks=0),
                html.Button("Reject latest plan", id="reject", n_clicks=0, style={"marginLeft": "8px"}),
                html.Div(id="action", style={"marginTop": "8px"}),
            ], style={**card, "flex": "1"}),
        ], style={"display": "flex", "gap": "10px", "flexWrap": "wrap"}),
        html.Div([
            html.H3("Paper Portfolio", style={"color": C["gold"]}),
            html.Div(id="portfolio"),
            html.Div([
                dcc.Dropdown(id="close-plan", placeholder="Select open plan", clearable=False, style={"minWidth": "220px", "color": "black"}),
                dcc.Input(id="close-price", type="number", min=0, step="any", placeholder="Exit price (blank = live)", debounce=True, style={"minWidth": "210px"}),
                html.Button("Close selected paper trade", id="close-trade", n_clicks=0),
            ], style={"display": "flex", "gap": "8px", "flexWrap": "wrap", "marginTop": "12px"}),
        ], style=card),
        html.Div(
            "Educational decision support only. Approval and manual close update a local paper portfolio; no exchange order is ever submitted.",
            style={"color": C["muted"], "fontSize": "12px"},
        ),
    ], style={"background": C["bg"], "color": C["text"], "minHeight": "100vh", "padding": "20px", "fontFamily": "Arial"})

    @app.callback(
        Output("status", "children"), Output("overview", "children"), Output("macro", "children"),
        Output("decision", "children"), Output("chart", "figure"), Output("signals", "children"),
        Output("news", "children"), Output("queue", "children"), Output("portfolio", "children"),
        Output("close-plan", "options"),
        Input("poll", "n_intervals"), Input("timeframe", "value"), Input("refresh", "n_clicks"),
        Input("portfolio-version", "data"),
    )
    def render(_, timeframe, __, ___):
        service.refresh()
        data = service.snapshot()
        ticker, cycle, decision = data.get("ticker", {}), data.get("cycle", {}), data.get("decision")
        status = f"{data.get('status')} • {data.get('at', 'waiting')}"
        overview = html.Div([
            html.H2(_money(ticker.get("price", 0)), style={"color": C["gold"]}),
            html.Div(f"24h {ticker.get('change_pct', 0):+.2f}%"),
            html.Div(f"Cycle {cycle.get('score', '—')}/10 — {cycle.get('phase', '—')}"),
        ])
        macro_data = data.get("macro", {})
        markets = macro_data.get("markets", {})
        market_rows = []
        for name, market in markets.items():
            if name not in ("SPX", "NASDAQ", "US30", "FTSE100", "DAX", "NIKKEI", "VIX", "DXY", "GOLD", "OIL"):
                continue
            vix_invalid = name == "VIX" and (not isinstance(market.get("price"), (int, float)) or market.get("price", 0) <= 0)
            if market.get("available") is False or vix_invalid:
                market_rows.append(html.Div(f"{name}: unavailable", style={"color": C["muted"]}))
            elif name == "VIX":
                market_rows.append(html.Div(f"VIX: {market.get('price', 0):.2f}"))
            else:
                market_rows.append(html.Div(f"{name}: {market.get('change', 0):+.2f}%"))
        macro_ui = html.Div([html.B(f"Macro: {macro_data.get('macro_bias', 'Unavailable')}")] + market_rows)

        frame = data.get("frames", {}).get(timeframe, pd.DataFrame())
        figure = go.Figure()
        if not frame.empty:
            figure.add_trace(go.Candlestick(x=frame.index, open=frame.open, high=frame.high, low=frame.low, close=frame.close, name="BTC"))
            for column, color in (("ema_21", C["gold"]), ("ema_50", C["green"]), ("ema_200", C["red"])):
                if column in frame:
                    figure.add_trace(go.Scatter(x=frame.index, y=frame[column], name=column, line={"color": color}))
        figure.update_layout(
            template="plotly_dark", paper_bgcolor=C["card"], plot_bgcolor=C["card"], height=550,
            xaxis_rangeslider_visible=False, margin={"l": 30, "r": 10, "t": 20, "b": 30},
            dragmode="pan", uirevision=f"btc-chart-{timeframe}",
            newshape={"line": {"color": C["gold"], "width": 2}},
        )

        signal_rows = [{"Signal": signal.name, "Read": signal.current_read, "Status": signal.status} for signal in data.get("signals", [])]
        signals_ui = dash_table.DataTable(
            signal_rows, [{"name": column, "id": column} for column in ("Signal", "Read", "Status")],
            style_header={"backgroundColor": "#202943", "color": "white"},
            style_data={"backgroundColor": C["card"], "color": "white"},
        )
        news_items = data.get("news", {}).get("all_news", [])
        news_ui = html.Ul([html.Li(f"[{item.category}/{item.impact}] {item.title}", style={"fontSize": "12px"}) for item in news_items[:8]]) if news_items else html.Div("No source currently available; retaining last successful data.")
        pending = service.decisions.get_pending_summary()
        queue = html.Div([
            html.Div(f"#{plan['id']} {plan['action']} @ {_money(plan['entry_price'])}") for plan in pending[-3:]
        ]) if pending else html.Div("No high-conviction plan awaiting approval.")
        current_price = ticker.get("price")
        portfolio = _portfolio_panel(service.decisions, current_price)
        open_options = [
            {"label": f"#{trade['id']} {trade['action']} @ {_money(trade['entry_price'])}", "value": trade["id"]}
            for trade in service.decisions.get_open_trades_summary(current_price)
        ]
        return status, overview, macro_ui, _decision_panel(decision), figure, signals_ui, news_ui, queue, portfolio, open_options

    @app.callback(
        Output("action", "children"), Output("portfolio-version", "data"),
        Input("approve", "n_clicks"), Input("reject", "n_clicks"), Input("close-trade", "n_clicks"),
        State("close-plan", "value"), State("close-price", "value"), State("portfolio-version", "data"),
        prevent_initial_call=True,
    )
    def paper_action(_, __, ___, close_id, close_price, version):
        trigger = dash.ctx.triggered_id
        if trigger == "close-trade":
            if close_id is None:
                return "Select an open paper plan to close.", version
            if close_price is None:
                close_price = service.snapshot().get("ticker", {}).get("price")
            result = service.decisions.close_trade(int(close_id), close_price, "Manual dashboard close")
            if not result.get("success"):
                return result.get("error", "Paper trade could not be closed."), version
            return f"Closed paper trade #{close_id}: {result['status']} ({result['pnl']:+.2f}%). No order was sent.", (version or 0) + 1

        pending = service.decisions.get_pending_summary()
        if not pending:
            return "Nothing to approve or reject.", version
        plan = pending[-1]
        if trigger == "approve":
            result = service.decisions.approve_trade(plan["id"])
            if not result.get("success"):
                return result.get("error", "Paper plan could not be approved."), version
            return f"Approved and opened paper plan #{plan['id']}. No order was sent.", (version or 0) + 1
        result = service.decisions.reject_trade(plan["id"], "Manual dashboard rejection")
        if not result.get("success"):
            return result.get("error", "Paper plan could not be rejected."), version
        return f"Rejected paper plan #{plan['id']}.", (version or 0) + 1

    return app
