"""Offline tests for brain/llm_brain.py — no network, no API keys required."""
from types import SimpleNamespace

from brain import llm_brain as llm_mod
from brain.llm_brain import LLMBrain


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _snapshot():
    decision = SimpleNamespace(
        action="LONG_CONFIDENT", confidence=72.0,
        reasoning=["4h trend aligned", "funding neutral"],
        entry_price=67000.0, stop_loss=66200.0,
        take_profit_1=68500.0, take_profit_2=69300.0, take_profit_3=70100.0,
        position_size=1.0, leverage=3, time_horizon="swing",
        risk_reward=2.1, invalidation="4h close below 66,200",
        trade_quality={
            "total": 64, "label": "High quality",
            "factors": [{"name": "trend", "score": 8, "reason": "4h/1d aligned up"}],
        },
    )
    signals = [
        SimpleNamespace(name="Trend Alignment", current_read="4h + 1d up", status="GREEN"),
        SimpleNamespace(name="Funding", current_read="slightly positive", status="YELLOW"),
        SimpleNamespace(name="CVD", current_read="aggressive sellers", status="RED"),
    ]
    return {
        "at": "2026-08-03 10:00:00 UTC",
        "ticker": {"price": 67234.0, "change_pct": 1.83},
        "cycle": {"score": 6, "phase": "Expansion"},
        "macro": {"macro_bias": "BULLISH", "macro_score": 2},
        "funding": {"current": 0.0123, "status": "Slightly Long Heavy"},
        "oi": {"change_24h": -2.5, "status": "Declining"},
        "ls": {"long_short_ratio": 1.42, "status": "Longs Crowded"},
        "book": {"imbalance": 0.31, "status": "Neutral"},
        "cvd": {"status": "Buying", "net_delta": 1240.0, "buy_pressure": 58.3,
                "n_trades": 1000, "divergence": False},
        "news": {"overall_sentiment": 0.12, "bull_news": 5, "bear_news": 3,
                 "news_count": 12,
                 "critical_news": [SimpleNamespace(title="ETF net inflows hit record")]},
        "polymarket": {"overall_bias": "BULLISH", "net_shift_pts": 3.2},
        "signals": signals,
        "decision": decision,
        "queued": {"status": "PENDING_APPROVAL"},
        "brain_status": {"status": "READY", "last_action": "LONG_CONFIDENT", "last_price": 67001.0},
        "portfolio_status": {"status": "SAFE"},
        "portfolio_exposure": {"open_count": 0, "total_risk": 0.0, "daily_pnl": 0.0},
    }


def test_prompt_contains_core_sections():
    p = LLMBrain.build_prompt(_snapshot())
    assert "BTC MARKET SNAPSHOT" in p
    assert "$67,234" in p and "+1.83%" in p
    assert "6/10" in p and "Expansion" in p
    assert "+0.0123%" in p                      # funding
    assert "ORDER FLOW (CVD): Buying" in p
    assert "1 GREEN / 1 RED / 1 YELLOW" in p
    assert "ETF net inflows hit record" in p
    assert "LONG_CONFIDENT" in p and "RR 2.10" in p
    assert "TRADE QUALITY: 64/100 (High quality)" in p
    assert "anti-whipsaw READY" in p
    assert "portfolio guardian SAFE" in p
    assert p.rstrip().endswith("required section headers.")


def test_prompt_handles_empty_snapshot():
    p = LLMBrain.build_prompt({})
    assert "unavailable" in p
    assert "TASK:" in p


def test_no_key_degrades_to_offline():
    brain = LLMBrain(provider="auto", groq_key="", gemini_key="")
    brief = brain.generate(_snapshot())
    assert brief["status"] == "OFFLINE"
    assert brief["provider"] is None
    assert "GROQ_API_KEY" in brief["text"] and "GEMINI_API_KEY" in brief["text"]
    assert "console.groq.com" in brief["text"]


def test_provider_off_never_calls_network():
    def _boom(*a, **k):
        raise AssertionError("network must not be called when LLM_PROVIDER=off")
    brain = LLMBrain(provider="off", groq_key="x", gemini_key="y")
    orig = llm_mod.session.post
    llm_mod.session.post = _boom
    try:
        brief = brain.generate(_snapshot())
    finally:
        llm_mod.session.post = orig
    assert brief["status"] == "OFFLINE"
    assert "disabled" in brief["error"]


def test_groq_success_path(monkeypatch):
    monkeypatch.setattr(
        llm_mod.session, "post",
        lambda *a, **k: _Resp({"choices": [{"message": {"content": "MARKET SUMMARY\nTest brief"}}]}),
    )
    brain = LLMBrain(provider="auto", groq_key="gsk_test", gemini_key="")
    brief = brain.generate(_snapshot())
    assert brief["status"] == "ONLINE"
    assert brief["provider"] == "groq"
    assert brief["model"] == brain.groq_model
    assert "Test brief" in brief["text"]
    assert brief["error"] is None


def test_gemini_fallback_when_groq_fails(monkeypatch):
    def _fake_post(url, **k):
        if "groq" in url:
            raise RuntimeError("groq 500")
        return _Resp({"candidates": [{"content": {"parts": [{"text": "Gemini brief"}]}}]})
    monkeypatch.setattr(llm_mod.session, "post", _fake_post)
    brain = LLMBrain(provider="auto", groq_key="gsk_test", gemini_key="gm_test")
    brief = brain.generate(_snapshot())
    assert brief["status"] == "ONLINE"
    assert brief["provider"] == "gemini"
    assert "Gemini brief" in brief["text"]


def test_all_providers_fail_degrades_offline(monkeypatch):
    def _fail(url, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(llm_mod.session, "post", _fail)
    brain = LLMBrain(provider="auto", groq_key="gsk_test", gemini_key="gm_test")
    brief = brain.generate(_snapshot())
    assert brief["status"] == "OFFLINE"
    assert "all providers failed" in brief["error"]


def test_stale_keeps_last_good_brief(monkeypatch):
    brain = LLMBrain(provider="auto", groq_key="", gemini_key="")
    brain._cache = {
        "status": "ONLINE", "text": "Previous good brief", "provider": "groq",
        "model": "m", "at": "earlier", "error": None,
    }
    brain._run(_snapshot())  # no keys -> would be OFFLINE, must merge into STALE
    cur = brain.current()
    assert cur["status"] == "STALE"
    assert "Previous good brief" in cur["text"]
    assert cur["provider"] == "groq"


def test_refresh_ttl_blocks_rapid_kicks():
    brain = LLMBrain(provider="auto", groq_key="", gemini_key="", refresh_seconds=3600)
    assert brain.refresh(_snapshot()) is True
    assert brain.refresh(_snapshot()) is False  # TTL not elapsed
    brain._lock.acquire(), brain._lock.release()  # let background thread settle is unnecessary; daemon


def test_current_includes_generating_flag():
    brain = LLMBrain(provider="auto", groq_key="", gemini_key="")
    cur = brain.current()
    assert cur["status"] == "IDLE"
    assert cur["generating"] is False
