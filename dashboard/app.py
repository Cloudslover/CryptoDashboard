"""Human-in-the-loop Dash interface. Approval records a plan only; it cannot place exchange orders."""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import threading, logging
import dash
from dash import Dash, dcc, html, Input, Output, State, dash_table
import plotly.graph_objects as go
import pandas as pd
from config import REFRESH_SECONDS
from data.indicators import TechnicalIndicators
from data.market_intelligence import MarketIntelligence
from brain.trade_quality import TradeQualityScorer

logger = logging.getLogger(__name__)
C = {"bg":"#0b1020","card":"#151c30","text":"#e7edf8","green":"#38d996","red":"#ff6b6b","gold":"#ffd166","muted":"#93a4c7"}

class BrainService:
    def __init__(self, fetcher, db, news, macro, polymarket, brain, decisions, cycle, signals, mtf, backtester, intelligence=None):
        self.fetcher, self.db, self.news, self.macro = fetcher, db, news, macro
        self.polymarket = polymarket
        self.brain, self.decisions, self.cycle, self.signals, self.mtf, self.backtester = brain, decisions, cycle, signals, mtf, backtester
        self.intelligence = intelligence or MarketIntelligence()
        self.trade_quality = TradeQualityScorer()
        self.lock, self.refresh_lock, self.cache = threading.Lock(), threading.Lock(), {"status":"Starting…"}
        self._news_at = self._macro_at = 0

    def refresh(self):
        # Browser callbacks and the monitor thread share one provider pipeline.
        if not self.refresh_lock.acquire(blocking=False):
            return
        try:
            with ThreadPoolExecutor(max_workers=7) as pool:
                fs = {tf:pool.submit(self.fetcher.get_ohlcv, tf, 500 if tf != "1d" else 730) for tf in ("1h","4h","1d")}
                ticker=pool.submit(self.fetcher.get_24h_ticker); funding=pool.submit(self.fetcher.get_funding_rate); oi=pool.submit(self.fetcher.get_open_interest)
                ls=pool.submit(self.fetcher.get_liquidations); fg=pool.submit(self.fetcher.get_fear_greed); book=pool.submit(self.fetcher.get_order_book_imbalance); cvd=pool.submit(self.fetcher.get_cvd)
                onchain=pool.submit(self.intelligence.onchain_snapshot); exchange_status=pool.submit(self.intelligence.exchange_status)
                # Polymarket runs in parallel so a slow/unreachable provider never stalls the rest.
                prev_chg = self.cache.get("ticker", {}).get("change_pct")
                poly = pool.submit(self.polymarket.get_summary, self.db, prev_chg)
                frames = {tf:TechnicalIndicators.add_all_indicators(f.result()) for tf,f in fs.items()}
                ticker, funding, oi, ls, fg, book, cvd = ticker.result(), funding.result(), oi.result(), ls.result(), fg.result(), book.result(), cvd.result()
            onchain, exchange_status = onchain.result(), exchange_status.result()
            # Offline/provider fallback: analyse retained local candles, but never invent a live price.
            for tf, frame in list(frames.items()):
                if frame.empty:
                    frames[tf] = TechnicalIndicators.add_all_indicators(self.db.load_candles(tf))
            if frames["1h"].empty or frames["4h"].empty or frames["1d"].empty or float(ticker.get("price", 0)) <= 0:
                raise RuntimeError("primary BTC market data unavailable; retaining last successful snapshot")
            now = datetime.now().timestamp()
            old = self.cache
            news = old.get("news", {}) if now-self._news_at < 300 else self.news.get_news_summary()
            macro = old.get("macro", {}) if now-self._macro_at < 300 else self.macro.get_macro_summary()
            if news is not old.get("news"): self._news_at = now
            if macro is not old.get("macro"): self._macro_at = now
            polymarket = poly.result()
            cycle = self.cycle.calculate_cycle_score(frames["1d"], funding, oi, fg, ls)
            signal_list = self.signals.generate_all_signals(frames["1h"], funding, oi, fg, ls, book, {"volume_change_pct":0}, cvd=cvd)
            mtf = self.mtf.analyze_all_timeframes(self.fetcher)
            market_data = {"price":ticker.get("price",0), "funding":funding, "oi":oi}
            decision = self.brain.make_decision(market_data, signal_list, mtf, news, macro, cycle, frames["1h"], polymarket_data=polymarket, cvd=cvd, trade_quality_scorer=self.trade_quality)
            queued = self.decisions.submit(decision)
            for tf, df in frames.items(): self.db.save_candles(tf, df)
            if news.get("all_news"): self.db.save_news(news["all_news"])
            if queued.get("status") == "PENDING_APPROVAL": self.db.save_decision(queued)
            with self.lock:
                self.cache = {"status":"Live", "at":datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"), "frames":frames, "ticker":ticker,"funding":funding,"oi":oi,"fg":fg,"ls":ls,"book":book,"cvd":cvd,"news":news,"macro":macro,"polymarket":polymarket,"cycle":cycle,"signals":signal_list,"mtf":mtf,"decision":decision,"queued":queued,"onchain":onchain,"exchange_status":exchange_status,"research_links":self.intelligence.research_links()}
        except Exception as exc:
            logger.exception("refresh failed")
            with self.lock: self.cache = {**self.cache, "status":f"Degraded: {exc}"}
        finally:
            self.refresh_lock.release()

    def snapshot(self):
        with self.lock: return self.cache.copy()

def create_app(service: BrainService) -> Dash:
    app = dash.Dash(__name__)
    app.title = "BTC AI Brain"
    card = {"background":C["card"],"padding":"14px","borderRadius":"8px","marginBottom":"10px"}
    app.layout = html.Div([
        dcc.Interval(id="poll", interval=REFRESH_SECONDS*1000, n_intervals=0), dcc.Store(id="selected", data="1h"),
        html.H1("₿ BTC AI BRAIN", style={"color":C["gold"],"marginBottom":"0"}), html.Div(id="status", style={"color":C["muted"],"marginBottom":"12px"}),
        html.Div([html.Div(id="decision", style={**card,"flex":"2","border":f"1px solid {C['gold']}"}), html.Div(id="overview",style={**card,"flex":"1"}), html.Div(id="macro",style={**card,"flex":"1"})], style={"display":"flex","gap":"10px"}),
        html.Div([html.Div(id="derivatives", style={**card,"flex":"1"}), html.Div(id="onchain", style={**card,"flex":"1"}), html.Div(id="health", style={**card,"flex":"1"})], style={"display":"flex","gap":"10px"}),
        html.Div([html.Div([html.H4("BTC futures workflow"), html.Div("News → macro calendar → derivatives → on-chain → sentiment → human approval", style={"color":C["muted"]}), html.Div(id="research", style={"marginTop":"8px"})], style={**card,"flex":"1"}), html.Div([html.H4("Data interpretation"), html.Div("Exchange deposits can imply sell pressure, withdrawals can imply custody or accumulation, and large mempool transactions are not proof of an exchange flow. Verify every alert in an explorer before acting.", style={"fontSize":"12px","color":C["muted"]})], style={**card,"flex":"1"})], style={"display":"flex","gap":"10px"}),
        html.Div([dcc.Dropdown([{"label":tf,"value":tf} for tf in ("1h","4h","1d")], "1h", id="timeframe", clearable=False, style={"width":"120px","color":"black"}), html.Button("Refresh now",id="refresh",n_clicks=0)], style={"display":"flex","gap":"10px"}),
        dcc.Graph(id="chart", config={"displayModeBar":False}, style=card),
        html.Div([html.Div([html.H4("Signals"),html.Div(id="signals")],style={**card,"flex":"1"}), html.Div([html.H4("News / event watch"),html.Div(id="news")],style={**card,"flex":"1"}), html.Div([html.H4("Human approval queue"),html.Div(id="queue"),html.Button("Approve latest plan",id="approve",n_clicks=0),html.Button("Reject latest plan",id="reject",n_clicks=0,style={"marginLeft":"8px"}),html.Div(id="action",style={"marginTop":"8px"})],style={**card,"flex":"1"})],style={"display":"flex","gap":"10px"}),
        html.Div([html.Div([html.H4("Order flow (CVD) — cumulative volume delta"),html.Div(id="cvd")],style={**card,"flex":"1"}), html.Div([html.H4("Trade quality — how strong is the evidence?"),html.Div(id="tradequality")],style={**card,"flex":"1","border":f"1px solid {C['gold']}"})],style={"display":"flex","gap":"10px"}),
        html.Div([html.H4("Prediction markets (Polymarket) — real-money sentiment"),html.Div(id="polymarket")],style={**card}),
        html.Div("Educational decision support only. Approval records a paper plan; it never submits an exchange order.",style={"color":C["muted"],"fontSize":"12px"})
    ], style={"background":C["bg"],"color":C["text"],"minHeight":"100vh","padding":"20px","fontFamily":"Arial"})

    @app.callback(Output("status","children"),Output("overview","children"),Output("macro","children"),Output("decision","children"),Output("derivatives","children"),Output("onchain","children"),Output("health","children"),Output("research","children"),Output("chart","figure"),Output("signals","children"),Output("news","children"),Output("queue","children"),Output("polymarket","children"),Output("cvd","children"),Output("tradequality","children"),Input("poll","n_intervals"),Input("timeframe","value"),Input("refresh","n_clicks"))
    def render(_, tf, __):
        service.refresh(); d=service.snapshot(); t=d.get("ticker",{}); cycle=d.get("cycle",{}); dec=d.get("decision")
        status=f"{d.get('status')} • {d.get('at','waiting')}"
        overview=html.Div([html.H2(f"${t.get('price',0):,.2f}",style={"color":C['gold']}),html.Div(f"24h {t.get('change_pct',0):+.2f}%"),html.Div(f"Cycle {cycle.get('score','—')}/10 — {cycle.get('phase','—')}")])
        macro=d.get("macro",{}); markets=macro.get("markets",{}); macro_ui=html.Div([html.B(f"Macro: {macro.get('macro_bias','Unavailable')}")] + [html.Div(f"{name}: {x.get('change',0):+.2f}%") for name,x in markets.items() if name in ("SPX","NASDAQ","US30","FTSE100","DAX","NIKKEI","DXY","GOLD","OIL")])
        if dec:
            color=C['green'] if 'LONG' in dec.action else C['red'] if 'SHORT' in dec.action else C['gold']
            decision=html.Div([html.H2(f"{dec.action} • {dec.confidence:.0f}%",style={"color":color}),html.Div(f"Entry ${dec.entry_price:,.0f} | Stop ${dec.stop_loss:,.0f} | TP1 ${dec.take_profit_1:,.0f} | R:R {dec.risk_reward:.2f}"),html.Div(" • ".join(dec.reasoning[:4]),style={"fontSize":"12px","color":C['muted']}),html.Div(f"Invalidation: {dec.invalidation}",style={"fontSize":"12px","color":C['red']})])
        else: decision=html.Div("Analyzing…")
        df=d.get('frames',{}).get(tf,pd.DataFrame()); fig=go.Figure()
        if not df.empty:
            fig.add_trace(go.Candlestick(x=df.index,open=df.open,high=df.high,low=df.low,close=df.close,name="BTC"))
            for col,color in (("ema_21",C['gold']),("ema_50",C['green']),("ema_200",C['red'])):
                if col in df: fig.add_trace(go.Scatter(x=df.index,y=df[col],name=col,line={"color":color}))
        fig.update_layout(template="plotly_dark",paper_bgcolor=C['card'],plot_bgcolor=C['card'],height=550,xaxis_rangeslider_visible=False,margin={"l":30,"r":10,"t":20,"b":30})
        sigrows=[{"Signal":s.name,"Read":s.current_read,"Status":s.status} for s in d.get('signals',[])]
        sigui=dash_table.DataTable(sigrows,[{"name":x,"id":x} for x in ("Signal","Read","Status")],style_header={"backgroundColor":"#202943","color":"white"},style_data={"backgroundColor":C['card'],"color":"white"})
        newsui=html.Ul([html.Li(f"[{n.category}/{n.impact}] {n.title}",style={"fontSize":"12px"}) for n in d.get('news',{}).get('all_news',[])[:8]]) or "No source currently available; retaining last successful data."
        pending=service.decisions.get_pending_summary(); queue=html.Div([html.Div(f"#{p['id']} {p['action']} @ ${p['entry_price']:,.0f}") for p in pending[-3:]]) if pending else html.Div("No high-conviction plan awaiting approval.")
        derivatives=html.Div([
            html.H4("Derivatives positioning"),
            html.Div(f"Funding {d.get('funding',{}).get('current',0):+.4f}% • {d.get('funding',{}).get('status','Unknown')}"),
            html.Div(f"Open interest {d.get('oi',{}).get('change_24h',0):+.2f}% • {d.get('oi',{}).get('status','Unknown')}"),
            html.Div(f"Long/short {d.get('ls',{}).get('long_short_ratio',1):.2f} • {d.get('ls',{}).get('status','Unknown')}"),
            html.Div(f"Order book {d.get('book',{}).get('imbalance',0):+.2f}% • {d.get('book',{}).get('status','Unknown')}", style={"color":C["muted"],"fontSize":"12px"})
        ])
        chain=d.get("onchain",{}); large=chain.get("large_mempool_tx",[])
        onchain=html.Div([
            html.H4("On-chain & whale watch"),
            html.Div(f"Block {chain.get('block_height','—')} • Mempool {chain.get('mempool_tx_count','—')} tx"),
            html.Div(f"Large pending transactions ≥100 BTC: {len(large)}"),
            html.Div("Mempool watch only — attribution is unavailable", style={"color":C["muted"],"fontSize":"12px"})
        ])
        health=d.get("exchange_status",{}); health_color=C["green"] if health.get("available") else C["red"]
        health_ui=html.Div([html.H4("Exchange status"), html.Div(health.get("message","Unknown"), style={"color":health_color}), html.Div(f"Public API latency: {health.get('ping_ms','—')} ms", style={"fontSize":"12px"}), html.Div("No API key; no orders can be sent", style={"color":C["muted"],"fontSize":"12px"})])
        links=d.get("research_links",{}); research=[]
        for group, items in links.items():
            research.append(html.Div([html.B(group.replace("_"," ").title()+": "), *[html.A(x["name"], href=x["url"], target="_blank", style={"color":C["gold"],"marginRight":"10px"}) for x in items]], style={"fontSize":"12px","marginBottom":"4px"}))
        poly=d.get("polymarket",{}); poly_markets=poly.get("markets",[])
        poly_head=html.Div([html.Span(f"Bias: {poly.get('overall_bias','—')}",style={"color":C['green'] if poly.get('overall_bias')=='BULLISH' else C['red'] if poly.get('overall_bias')=='BEARISH' else C['gold']}),html.Span(f" • net {poly.get('net_shift_pts',0):+.1f}pt",style={"color":C['muted']}),html.Span(f" • {poly.get('correlation_note','')}",style={"color":C['muted']})],style={"fontSize":"12px","marginBottom":"6px"})
        if poly_markets:
            rows=[{"Market":m["question"],"Type":m["category"],"P(Yes)":f"{m['yes_price']:.0f}%","Δ since 1d":(f"{m['change_1d']:+.1f}pt" if m.get("change_1d") is not None else "—")} for m in poly_markets[:10]]
            poly_table=dash_table.DataTable(rows,[{"name":c,"id":c} for c in ("Market","Type","P(Yes)","Δ since 1d")],style_header={"backgroundColor":"#202943","color":"white"},style_data={"backgroundColor":C['card'],"color":"white"},style_data_conditional=[{"if":{"column_id":"Δ since 1d"},"color":C['green']}])
            poly=html.Div([poly_head,poly_table])
        else:
            poly=html.Div([poly_head,html.Div("No Polymarket markets available yet (provider may be unreachable); retaining last snapshot.",style={"color":C['muted'],"fontSize":"12px"})])
        cvd=d.get("cvd",{}); cvd_bull=cvd.get("status","Unknown") in ("Strong Buying","Buying")
        cvd_color=C['green'] if cvd_bull else C['red'] if cvd.get("status","") in ("Strong Selling","Selling") else C['gold']
        cvd_ui=html.Div([
            html.Div([html.Span(f"{cvd.get('status','Unknown')}",style={"color":cvd_color,"fontWeight":"bold"}),html.Span(f" • Δ {cvd.get('net_delta',0):,.0f} BTC",style={"color":C['muted']})]),
            html.Div(f"Buy {cvd.get('buy_volume',0):,.0f} vs Sell {cvd.get('sell_volume',0):,.0f} BTC",style={"fontSize":"12px"}),
            html.Div(f"Buy pressure {cvd.get('buy_pressure',0):.1f}% • {cvd.get('n_trades',0)} trades",style={"fontSize":"12px","color":C['muted']}),
            html.Div(("⚠ Divergence: price and delta disagree" if cvd.get("divergence") else "No price/delta divergence"),style={"fontSize":"12px","color":C['red'] if cvd.get("divergence") else C['muted']}),
        ])
        tq=(dec.trade_quality if dec and getattr(dec,"trade_quality",None) else None)
        if tq:
            tq_color=C['green'] if tq["total"]>=60 else C['red'] if tq["total"]<45 else C['gold']
            rows=[{"Factor":f["name"],"Score":f"{f['score']:.0f}/10","Reason":f["reason"]} for f in tq["factors"]]
            tq_table=dash_table.DataTable(rows,[{"name":x,"id":x} for x in ("Factor","Score","Reason")],style_header={"backgroundColor":"#202943","color":"white"},style_data={"backgroundColor":C['card'],"color":"white"},style_data_conditional=[{"if":{"column_id":"Score"},"color":C['gold']}])
            tq_ui=html.Div([html.H3(f"Trade Quality {tq['total']:.0f}/100 — {tq['label']}",style={"color":tq_color}),tq_table])
        else:
            tq_ui=html.Div("Trade quality not computed yet.",style={"color":C['muted']})
        return status,overview,macro_ui,decision,derivatives,onchain,health_ui,research,fig,sigui,newsui,queue,poly,cvd_ui,tq_ui

    @app.callback(Output("action","children"),Input("approve","n_clicks"),Input("reject","n_clicks"),prevent_initial_call=True)
    def action(yes,no):
        from dash import ctx
        pending=service.decisions.get_pending_summary()
        if not pending:return "Nothing to approve or reject."
        plan=pending[-1]
        if ctx.triggered_id=="approve": service.decisions.approve_trade(plan['id']); return f"Recorded paper-plan approval for #{plan['id']}. No order was sent."
        service.decisions.reject_trade(plan['id'],"Manual rejection"); return f"Rejected plan #{plan['id']}."
    return app
