"""brain/llm_brain.py — LLM "AI Brain" assistant.

Reads the live dashboard snapshot (price, cycle, macro, derivatives, order
flow, signals, news, Polymarket, local strategy read, trade-quality breakdown,
anti-whipsaw / portfolio-guardian state) and asks an external LLM to write a
plain-English market-intelligence brief.

Design goals:
- **Any free key works.** Tries Groq first (OpenAI-compatible chat API),
  falls back to Google Gemini. ``LLM_PROVIDER=auto|groq|gemini|off``.
- **Explainable, not a trade bot.** The system prompt forbids buy/sell
  commands, position sizing and leverage advice; output is a fixed
  sectioned brief (summary → derivatives → order flow → quality → risk →
  conflicts → watch list → bottom line).
- **Never blocks the dashboard.** Generation runs on a daemon thread behind
  a TTL cache; with no key or a provider error the panel degrades to an
  explicit ASSISTANT OFFLINE notice with setup hints. ``generate()`` never
  raises.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from config import (
    LLM_PROVIDER,
    GROQ_API_KEY,
    GROQ_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    LLM_REFRESH_SECONDS,
    LLM_MAX_TOKENS,
    LLM_TIMEOUT_SECONDS,
)
from utils.http import session

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

SYSTEM_PROMPT = """You are the AI Brain analyst inside a BTC market-intelligence terminal.
Read the data snapshot below and write a plain-English situation report for a human trader.

STRICT RULES:
- NEVER issue buy/sell commands, order entries, position sizing, or leverage advice.
- Describe what the data says, where signals agree, and where they conflict.
- Be honest about uncertainty and about unavailable/missing data.
- This is educational decision support, not financial advice.
- Keep the whole brief under ~250 words. Short lines, no fluff.

Use EXACTLY these section headers, in this order, one or two short lines each:
MARKET SUMMARY
DERIVATIVES
ORDER FLOW
TRADE QUALITY
RISK ASSESSMENT
CONFLICTS
WHAT TO WATCH
BOTTOM LINE"""

OFFLINE_HINT = (
    "Set an API key in .env and restart to bring the assistant online:\n"
    "  • GROQ_API_KEY  — free at console.groq.com  (tried first)\n"
    "  • GEMINI_API_KEY — free at aistudio.google.com (automatic fallback)\n"
    "LLM_PROVIDER=auto tries Groq, then Gemini."
)


def _money(x) -> str:
    try:
        return f"${float(x):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _pct(x, digits: int = 2) -> str:
    try:
        return f"{float(x):+.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


class LLMBrain:
    """Non-blocking LLM brief generator with TTL cache and provider fallback."""

    def __init__(self, provider: str | None = None, groq_key: str | None = None,
                 gemini_key: str | None = None, groq_model: str | None = None,
                 gemini_model: str | None = None, refresh_seconds: int | None = None,
                 max_tokens: int | None = None, timeout: int | None = None):
        self.provider = (provider or LLM_PROVIDER or "auto").strip().lower()
        self.groq_key = groq_key if groq_key is not None else GROQ_API_KEY
        self.gemini_key = gemini_key if gemini_key is not None else GEMINI_API_KEY
        self.groq_model = groq_model or GROQ_MODEL
        self.gemini_model = gemini_model or GEMINI_MODEL
        self.refresh_seconds = int(refresh_seconds if refresh_seconds is not None else LLM_REFRESH_SECONDS)
        self.max_tokens = int(max_tokens if max_tokens is not None else LLM_MAX_TOKENS)
        self.timeout = int(timeout if timeout is not None else LLM_TIMEOUT_SECONDS)

        self._lock = threading.Lock()
        self._running = False
        self._last_attempt = 0.0
        self._cache: dict = {
            "status": "IDLE",
            "text": "Assistant warming up — brief appears after the first market refresh.",
            "provider": None,
            "model": None,
            "at": None,
            "error": None,
        }

    # ── prompt construction (pure, fully offline / testable) ─────────────
    @staticmethod
    def build_prompt(d: dict) -> str:
        t = d.get("ticker", {}) or {}
        cycle = d.get("cycle", {}) or {}
        macro = d.get("macro", {}) or {}
        fund = d.get("funding", {}) or {}
        oi = d.get("oi", {}) or {}
        ls = d.get("ls", {}) or {}
        book = d.get("book", {}) or {}
        cvd = d.get("cvd", {}) or {}
        news = d.get("news", {}) or {}
        poly = d.get("polymarket", {}) or {}
        sigs = d.get("signals", []) or []
        dec = d.get("decision")
        queued = d.get("queued", {}) or {}
        brain_status = d.get("brain_status", {}) or {}
        port_status = d.get("portfolio_status", {}) or {}
        port_exp = d.get("portfolio_exposure", {}) or {}

        by_status = {"GREEN": [], "RED": [], "YELLOW": []}
        for s in sigs:
            status = getattr(s, "status", None) or (s.get("status") if isinstance(s, dict) else "YELLOW")
            name = getattr(s, "name", None) or s.get("name", "?")
            read = getattr(s, "current_read", None) or s.get("current_read", "")
            by_status.setdefault(status, []).append(f"{name} ({read})")
        sig_line = f"{len(by_status.get('GREEN', []))} GREEN / {len(by_status.get('RED', []))} RED / {len(by_status.get('YELLOW', []))} YELLOW"
        sig_detail = "; ".join(
            f"{k}: {', '.join(v[:4])}" for k, v in by_status.items() if v
        ) or "unavailable"

        if dec is not None:
            g = lambda k, default=None: getattr(dec, k, default) if not isinstance(dec, dict) else dec.get(k, default)
            dec_line = (
                f"{g('action', '—')} | confidence {float(g('confidence', 0) or 0):.0f}% | RR {float(g('risk_reward', 0) or 0):.2f} | "
                f"entry {_money(g('entry_price'))} SL {_money(g('stop_loss'))} "
                f"TP1 {_money(g('take_profit_1'))} TP2 {_money(g('take_profit_2'))} TP3 {_money(g('take_profit_3'))} | "
                f"risk {g('position_size', '—')}% lev {g('leverage', '—')}x horizon {g('time_horizon', '—')} | "
                f"invalidation: {g('invalidation', '—')}"
            )
            reasoning = g("reasoning", []) or []
            reasoning_line = " • ".join(str(r) for r in reasoning[:6]) or "—"
            tq = g("trade_quality")
        else:
            dec_line = "unavailable (analysis pending)"
            reasoning_line, tq = "—", None

        if tq:
            tq_factors = "; ".join(
                f"{f.get('name', '?')} {float(f.get('score', 0)):.0f}/10: {str(f.get('reason', ''))[:60]}"
                for f in (tq.get("factors") or [])[:8]
            )
            tq_line = f"{float(tq.get('total', 0)):.0f}/100 ({tq.get('label', '—')}) — {tq_factors}"
        else:
            tq_line = "unavailable"

        crit = []
        for n in (news.get("critical_news") or [])[:3]:
            title = getattr(n, "title", None) or (n.get("title") if isinstance(n, dict) else None)
            if title:
                crit.append(title)
        crit_line = " | ".join(crit) or "none critical"

        stability = "SUPPRESSED: " + str(queued.get("reason", "")) if queued.get("status") == "SUPPRESSED" \
            else f"{brain_status.get('status', '—')} (last: {brain_status.get('last_action', '—')} @ {_money(brain_status.get('last_price', 0))})"

        return (
            f"BTC MARKET SNAPSHOT — {d.get('at', '—')}\n"
            f"PRICE: {_money(t.get('price'))} (24h {_pct(t.get('change_pct'))}) | market cycle {cycle.get('score', '—')}/10 ({cycle.get('phase', '—')})\n"
            f"MACRO: bias {macro.get('macro_bias', '—')} (score {macro.get('macro_score', '—')}) | "
            f"news sentiment {news.get('overall_sentiment', 0):+.2f} ({news.get('bull_news', 0)} bull / {news.get('bear_news', 0)} bear of {news.get('news_count', 0)}) | "
            f"polymarket {poly.get('overall_bias', '—')} (net {float(poly.get('net_shift_pts', 0) or 0):+.1f}pt)\n"
            f"DERIVATIVES: funding {_pct(fund.get('current'), 4)} ({fund.get('status', '—')}) | "
            f"open interest 24h {_pct(oi.get('change_24h'))} ({oi.get('status', '—')}) | "
            f"long/short ratio {ls.get('long_short_ratio', '—')} ({ls.get('status', '—')}) | "
            f"order-book imbalance {_pct(book.get('imbalance'))}\n"
            f"ORDER FLOW (CVD): {cvd.get('status', '—')} | net delta {float(cvd.get('net_delta', 0) or 0):,.0f} | "
            f"buy pressure {float(cvd.get('buy_pressure', 0) or 0):.1f}% over {cvd.get('n_trades', 0)} trades | "
            f"divergence: {'YES (absorption warning)' if cvd.get('divergence') else 'no'}\n"
            f"TECHNICAL SIGNALS: {sig_line}\n  {sig_detail}\n"
            f"NEWS HEADLINES (critical): {crit_line}\n"
            f"LOCAL RULE-BASED STRATEGY READ (not yours — critique it): {dec_line}\n"
            f"STRATEGY REASONING: {reasoning_line}\n"
            f"TRADE QUALITY: {tq_line}\n"
            f"GUARDS: anti-whipsaw {stability} | queue {queued.get('status', '—')} | "
            f"portfolio guardian {port_status.get('status', '—')} ({port_exp.get('open_count', 0)} open, "
            f"risk {float(port_exp.get('total_risk', 0) or 0):.2f}%, daily {float(port_exp.get('daily_pnl', 0) or 0):+.2f}%)\n"
            "TASK: Write the brief using the required section headers."
        )

    # ── provider calls ────────────────────────────────────────────────────
    def _call_groq(self, prompt: str) -> str:
        r = session.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {self.groq_key}", "Content-Type": "application/json"},
            json={
                "model": self.groq_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": self.max_tokens,
                "temperature": 0.3,
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        return str(r.json()["choices"][0]["message"]["content"]).strip()

    def _call_gemini(self, prompt: str) -> str:
        r = session.post(
            GEMINI_URL.format(model=self.gemini_model),
            headers={"x-goog-api-key": self.gemini_key, "Content-Type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"maxOutputTokens": self.max_tokens, "temperature": 0.3},
            },
            timeout=self.timeout,
        )
        r.raise_for_status()
        data = r.json()
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return " ".join(str(p.get("text", "")) for p in parts).strip()

    # ── generation (never raises) ─────────────────────────────────────────
    def _offline(self, reason: str) -> dict:
        return {
            "status": "OFFLINE",
            "text": f"ASSISTANT OFFLINE — {reason}\n{OFFLINE_HINT}",
            "provider": None,
            "model": None,
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
            "error": reason,
        }

    def generate(self, snapshot: dict) -> dict:
        """Synchronously generate a brief. Tries providers in preference order."""
        pref = self.provider
        if pref == "off":
            return self._offline("disabled by LLM_PROVIDER=off.")
        order = {"groq": ["groq"], "gemini": ["gemini"]}.get(pref, ["groq", "gemini"])
        keys = {"groq": self.groq_key, "gemini": self.gemini_key}
        models = {"groq": self.groq_model, "gemini": self.gemini_model}
        callers = {"groq": self._call_groq, "gemini": self._call_gemini}

        try_order = [p for p in order if keys.get(p)]
        if not try_order:
            return self._offline(f"no API key configured for provider '{pref}'.")

        prompt = self.build_prompt(snapshot)
        last_error = None
        for p in try_order:
            try:
                text = callers[p](prompt)
                if not text:
                    raise RuntimeError("empty response")
                return {
                    "status": "ONLINE",
                    "text": text,
                    "provider": p,
                    "model": models[p],
                    "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001 — degrade gracefully by design
                last_error = f"{p}: {exc}"
                logger.warning("LLM provider %s failed: %s", p, exc)
        return self._offline(f"all providers failed ({last_error}).")

    # ── background refresh with TTL ───────────────────────────────────────
    def refresh(self, snapshot: dict, force: bool = False) -> bool:
        """Kick a background generation if the TTL elapsed. Returns True if started."""
        now = time.time()
        with self._lock:
            if self._running:
                return False
            if not force and now - self._last_attempt < self.refresh_seconds:
                return False
            self._running = True
            self._last_attempt = now
        threading.Thread(target=self._run, args=(snapshot,), daemon=True, name="llm-brain").start()
        return True

    def _run(self, snapshot: dict) -> None:
        try:
            brief = self.generate(snapshot)
        except Exception as exc:  # absolute guard — dashboard must never die
            brief = self._offline(f"unexpected error: {exc}")
        with self._lock:
            old = self._cache
            # Keep the last good brief visible when a refresh fails (STALE > OFFLINE).
            if brief["status"] == "OFFLINE" and old.get("status") == "ONLINE" and old.get("text"):
                brief = {**old, "status": "STALE", "error": brief.get("error"),
                         "text": old["text"] + f"\n\n⚠ refresh failed ({brief.get('error')}) — showing previous brief."}
            self._cache = brief
            self._running = False

    def current(self) -> dict:
        with self._lock:
            out = dict(self._cache)
            out["generating"] = self._running
            return out
