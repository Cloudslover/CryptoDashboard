# data/database.py
import sqlite3
import pandas as pd
import json
import os
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "btc_brain.db")


class Database:

    def __init__(self):
        self.path = os.path.abspath(DB_PATH)
        self._init()
        logger.info(f"[OK] Database: {self.path}")

    def _conn(self):
        return sqlite3.connect(self.path, timeout=30)

    def _init(self):
        # Enable WAL for better concurrency
        with self._conn() as c:
            try:
                c.execute("PRAGMA journal_mode=WAL;")
            except Exception:
                pass
            c.executescript("""
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, timeframe TEXT,
                open REAL, high REAL, low REAL, close REAL, volume REAL,
                rsi REAL, ema_50 REAL, ema_200 REAL,
                funding REAL, oi_usd REAL, fear_greed INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS candles (
                timeframe TEXT NOT NULL, timestamp TEXT NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                close REAL NOT NULL, volume REAL NOT NULL,
                PRIMARY KEY (timeframe, timestamp));
            CREATE TABLE IF NOT EXISTS news_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, title TEXT, summary TEXT,
                source TEXT, category TEXT,
                sentiment REAL, impact TEXT, url TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS ai_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, action TEXT, confidence REAL,
                entry_price REAL, stop_loss REAL,
                take_profit_1 REAL, take_profit_2 REAL, take_profit_3 REAL,
                position_size REAL, leverage INTEGER,
                reasoning TEXT, invalidation TEXT,
                trade_quality TEXT,
                status TEXT DEFAULT "PENDING",
                exit_price REAL DEFAULT 0, pnl_pct REAL DEFAULT 0,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS polymarket_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, slug TEXT, question TEXT, category TEXT,
                yes_price REAL, volume REAL, liquidity REAL, end_date TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS idx_polymarket_slug_ts
                ON polymarket_log (slug, timestamp);
            CREATE TABLE IF NOT EXISTS macro_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                spx_change REAL, gold_change REAL, oil_change REAL,
                dxy_change REAL, vix_price REAL,
                fed_rate REAL, yield_10y REAL, macro_bias TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS signal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, cycle_score REAL, phase TEXT,
                news_sentiment REAL, macro_bias TEXT, mtf_bias REAL,
                green_signals INTEGER, red_signals INTEGER, funding_rate REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date TEXT, strategy TEXT,
                start_date TEXT, end_date TEXT,
                total_trades INTEGER, win_rate REAL,
                profit_factor REAL, total_return REAL,
                max_drawdown REAL, sharpe_ratio REAL, params TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP);
            """)
            # Migration for DBs created before the trade_quality column existed.
            try:
                c.execute("ALTER TABLE ai_decisions ADD COLUMN trade_quality TEXT")
            except Exception:
                pass  # column already exists

    def save_candles(self, timeframe, candles: pd.DataFrame) -> int:
        """Persist exchange OHLCV idempotently, so restarts retain historical data."""
        if candles is None or candles.empty:
            return 0
        rows = [(timeframe, str(index), float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume))
                for index, row in candles[["open", "high", "low", "close", "volume"]].iterrows()]
        try:
            with self._conn() as conn:
                conn.executemany("INSERT OR REPLACE INTO candles (timeframe,timestamp,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)", rows)
            return len(rows)
        except Exception as exc:
            logger.warning("save_candles failed: %s", exc)
            return 0

    def load_candles(self, timeframe="1h", limit=2000) -> pd.DataFrame:
        try:
            with self._conn() as conn:
                df = pd.read_sql_query("SELECT timestamp,open,high,low,close,volume FROM candles WHERE timeframe=? ORDER BY timestamp DESC LIMIT ?", conn, params=(timeframe, limit))
            if df.empty: return df
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            return df.set_index("timestamp").sort_index()
        except Exception as exc:
            logger.warning("load_candles failed: %s", exc); return pd.DataFrame()

    def save_price_snapshot(self, d):
        try:
            with self._conn() as c:
                c.execute("""INSERT INTO price_history
                    (timestamp,timeframe,open,high,low,close,volume,
                     rsi,ema_50,ema_200,funding,oi_usd,fear_greed)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (datetime.now().isoformat(), d.get("timeframe","1h"),
                     d.get("open",0),   d.get("high",0),
                     d.get("low",0),    d.get("close",0),
                     d.get("volume",0), d.get("rsi",0),
                     d.get("ema_50",0), d.get("ema_200",0),
                     d.get("funding",0),d.get("oi_usd",0),
                     d.get("fear_greed",50)))
        except Exception as e:
            logger.exception("save_price_snapshot failed: %s", e)

    def save_news(self, items):
        try:
            with self._conn() as c:
                for n in items:
                    try:
                        ts = (n.timestamp.isoformat()
                              if hasattr(n.timestamp,"isoformat")
                              else str(n.timestamp))
                        c.execute("""INSERT INTO news_log
                            (timestamp,title,summary,source,category,sentiment,impact,url)
                            VALUES(?,?,?,?,?,?,?,?)""",
                            (ts, n.title[:200], n.summary[:500],
                             n.source, n.category,
                             n.sentiment, n.impact, n.url))
                    except Exception:
                        pass
        except Exception as e:
            logger.exception("save_news failed: %s", e)

    def save_decision(self, d) -> int:
        try:
            with self._conn() as c:
                tq = d.get("trade_quality")
                cur = c.execute("""INSERT INTO ai_decisions
                    (timestamp,action,confidence,entry_price,stop_loss,
                     take_profit_1,take_profit_2,take_profit_3,
                     position_size,leverage,reasoning,invalidation,trade_quality,status)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (d.get("timestamp"), d.get("action"),
                     d.get("confidence"), d.get("entry_price"),
                     d.get("stop_loss"),  d.get("take_profit_1"),
                     d.get("take_profit_2"), d.get("take_profit_3"),
                     d.get("position_size"), d.get("leverage"),
                     json.dumps(d.get("reasoning",[])),
                     d.get("invalidation",""),
                     json.dumps(tq) if tq else None,
                     d.get("status","PENDING")))
                return cur.lastrowid
        except Exception as e:
            logger.exception("save_decision failed: %s", e)
            return 0

    def update_decision(self, did, status, exit_price=0, pnl=0, notes=""):
        try:
            with self._conn() as c:
                c.execute("""UPDATE ai_decisions
                    SET status=?,exit_price=?,pnl_pct=?,notes=? WHERE id=?""",
                    (status, exit_price, pnl, notes, did))
        except Exception as e:
            logger.exception("update_decision failed: %s", e)

    def save_polymarket(self, markets):
        """Persist a Polymarket snapshot so probability shifts can be computed
        later (e.g. 'how did implied probability move since ~24h ago')."""
        if not markets:
            return
        ts = datetime.now().isoformat()
        try:
            with self._conn() as c:
                c.executemany(
                    """INSERT INTO polymarket_log
                       (timestamp,slug,question,category,yes_price,volume,liquidity,end_date)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    [(ts, m.slug, (m.question or "")[:300], m.category,
                      m.yes_price, m.volume, m.liquidity, m.end_date)
                     for m in markets])
        except Exception as exc:
            logger.warning("save_polymarket failed: %s", exc)

    def load_polymarket_history(self):
        """Return all stored Polymarket snapshots ordered by time."""
        try:
            with self._conn() as c:
                return pd.read_sql_query(
                    """SELECT timestamp,slug,question,category,yes_price,volume,
                              liquidity,end_date
                       FROM polymarket_log ORDER BY timestamp ASC""", c)
        except Exception as exc:
            logger.warning("load_polymarket_history failed: %s", exc)
            return pd.DataFrame()

    def save_macro(self, macro):
        try:
            stocks  = macro.get("stocks",{})
            indices = stocks.get("indices",{})
            with self._conn() as c:
                c.execute("""INSERT INTO macro_log
                    (timestamp,spx_change,gold_change,oil_change,
                     dxy_change,vix_price,fed_rate,yield_10y,macro_bias)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (datetime.now().isoformat(),
                     indices.get("SPX",{}).get("change",0),
                     macro.get("gold",{}).get("change",0),
                     macro.get("oil",{}).get("change",0),
                     indices.get("DXY",{}).get("change",0),
                     indices.get("VIX",{}).get("price",20),
                     macro.get("fed",{}).get("fed_funds_rate",5.25),
                     macro.get("fed",{}).get("yield_10y",4.5),
                     macro.get("macro_bias","NEUTRAL")))
        except Exception as e:
            logger.exception("save_macro failed: %s", e)

    def save_signal_snapshot(self, d):
        try:
            with self._conn() as c:
                c.execute("""INSERT INTO signal_log
                    (timestamp,cycle_score,phase,news_sentiment,macro_bias,
                     mtf_bias,green_signals,red_signals,funding_rate)
                    VALUES(?,?,?,?,?,?,?,?,?)""",
                    (datetime.now().isoformat(),
                     d.get("cycle_score",5), d.get("phase",""),
                     d.get("news_sentiment",0), d.get("macro_bias","NEUTRAL"),
                     d.get("mtf_bias",0), d.get("green_signals",0),
                     d.get("red_signals",0), d.get("funding_rate",0)))
        except Exception as e:
            logger.exception("save_signal_snapshot failed: %s", e)

    def save_backtest(self, r):
        try:
            with self._conn() as c:
                c.execute("""INSERT INTO backtest_results
                    (run_date,strategy,start_date,end_date,total_trades,
                     win_rate,profit_factor,total_return,max_drawdown,sharpe_ratio,params)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (datetime.now().isoformat(),
                     r.get("strategy"), r.get("start_date"), r.get("end_date"),
                     r.get("total_trades"), r.get("win_rate"),
                     r.get("profit_factor"), r.get("total_return"),
                     r.get("max_drawdown"), r.get("sharpe_ratio"),
                     json.dumps(r.get("params",{}))))
        except Exception as e:
            logger.exception("save_backtest failed: %s", e)

    def get_price_history(self, tf="1h", days=30):
        try:
            with self._conn() as c:
                return pd.read_sql_query("""
                    SELECT * FROM price_history
                    WHERE timeframe=? AND timestamp>=datetime("now",?)
                    ORDER BY timestamp ASC""",
                    c, params=(tf, f"-{days} days"))
        except Exception as e:
            logger.exception("get_price_history failed: %s", e)
            return pd.DataFrame()

    def get_recent_news(self, hours=24):
        try:
            with self._conn() as c:
                return pd.read_sql_query("""
                    SELECT * FROM news_log
                    WHERE created_at>=datetime("now",?)
                    ORDER BY sentiment DESC""",
                    c, params=(f"-{hours} hours",))
        except Exception as e:
            logger.exception("get_recent_news failed: %s", e)
            return pd.DataFrame()

    def get_decision_history(self, limit=50):
        try:
            with self._conn() as c:
                return pd.read_sql_query("""
                    SELECT * FROM ai_decisions
                    ORDER BY created_at DESC LIMIT ?""",
                    c, params=(limit,))
        except Exception as e:
            logger.exception("get_decision_history failed: %s", e)
            return pd.DataFrame()

    def get_backtest_history(self):
        try:
            with self._conn() as c:
                return pd.read_sql_query("""
                    SELECT * FROM backtest_results
                    ORDER BY created_at DESC""", c)
        except Exception as e:
            logger.exception("get_backtest_history failed: %s", e)
            return pd.DataFrame()
