import asyncio
from datetime import datetime, timedelta, timezone

from app.config import load_settings
from app.engine import TradingEngine, _AIPrecheckEntry
from app.models import AIReview, DirectionScore, OptionCandidate, TechnicalSnapshot


def snapshot(*, phase="ARMED", trend15="bullish", regime="TRENDING"):
    now = datetime.now(timezone.utc)
    return TechnicalSnapshot(
        symbol="AAPL", timestamp=now, session="prime", price=200.10,
        ema_fast=200.05, ema_medium=199.90, ema_slow=199.50, vwap=199.95,
        rsi=61, macd_hist=.2, macd_hist_change=.05, atr=1.0,
        relative_volume=1.7, volume_acceleration=1.3,
        trend_1m="bullish", trend_5m="bullish", trend_15m=trend15,
        breakout_phase_call=phase, breakout_trigger_call=200.0,
        market_regime=regime, trend_efficiency_5m=.62, vwap_crosses_recent=1,
        bar_body_ratio=.65, close_location=.78, fast_ema_slope_atr=.08, bars_ready=100,
    )


def score(direction="CALL", *, phase="ARMED", total=84):
    return DirectionScore(
        symbol="AAPL", direction=direction, total=total,
        trend=24, structure=22, volume=13, momentum=14, levels=9, market_context=7,
        setup="BREAKOUT_IGNITION", setup_score=94,
        invalidation_underlying=199.5 if direction == "CALL" else 200.5,
        target_underlying=202 if direction == "CALL" else 198,
        trigger_underlying=200.0,
        chase_limit_underlying=200.55 if direction == "CALL" else 199.45,
        breakout_phase=phase, early_entry_eligible=phase == "TRIGGERED",
        chase_utilization=.25 if phase == "TRIGGERED" else None,
        regime_eligible=True,
    )


def option(ask=1.02):
    return OptionCandidate(
        symbol="AAPL", contract_symbol="AAPL260918C00200000", direction="CALL",
        expiration="2026-09-18", dte=1, strike=200,
        bid=ask-.02, ask=ask, mid=ask-.01, spread_pct=2.0,
        option_score=85, estimated_emergency_risk_usd=20.4,
        quote_source="webull_openapi", webull_bid=ask-.02, webull_ask=ask,
    )


def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "v141.sqlite3"))
    monkeypatch.setenv("DATA_MODE", "live")
    monkeypatch.setenv("ENABLE_AI_REVIEW", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("ALPACA_API_KEY", "a")
    monkeypatch.setenv("ALPACA_API_SECRET", "b")
    return TradingEngine(load_settings())


def test_compact_feature_fingerprint_ignores_expected_armed_to_triggered_transition(tmp_path, monkeypatch):
    e = engine(tmp_path, monkeypatch)
    armed = snapshot(phase="ARMED")
    trig = armed.model_copy(update={"breakout_phase_call": "TRIGGERED"})
    a = score(phase="ARMED", total=84); t = score(phase="TRIGGERED", total=84)
    other = score("PUT", phase="NONE", total=12)
    ctx = {"is_etf": False, "effective_risk_mode": "normal"}
    va = e.ai.build_feature_vector(armed, a, other, option(), "normal", ctx)
    vt = e.ai.build_feature_vector(trig, t, other, option(), "normal", ctx)
    assert "technical_snapshot" not in va
    assert e.ai.feature_fingerprint(va) == e.ai.feature_fingerprint(vt)


def test_fresh_matching_precheck_is_reused_without_trigger_time_ai_call(tmp_path, monkeypatch):
    async def run():
        e = engine(tmp_path, monkeypatch)
        snap = snapshot(phase="TRIGGERED")
        call, put, c = score(phase="TRIGGERED", total=84), score("PUT", phase="NONE", total=12), option()
        vector = e._ai_vector(snap, call, put, c, "normal", "")
        fp = e.ai.feature_fingerprint(vector)
        reviewed = datetime.now(timezone.utc)
        pre = AIReview(verdict="APPROVE", confidence=.76, summary="coherent", reason_code="CLEAN_ALIGNMENT", review_mode="PRECHECK", reviewed_at=reviewed, feature_fingerprint=fp)
        e.ai_prechecks["AAPL"] = _AIPrecheckEntry(reviewed, reviewed+timedelta(seconds=25), vector, fp, pre, c.contract_symbol, c.ask, c.spread_pct)
        async def should_not_call(*a, **k):
            raise AssertionError("trigger-time AI call should not run for a fresh matching cache")
        e.ai.review = should_not_call
        e.ai.micro_review = should_not_call
        out = await e._resolve_ai_final_review(snap, call, put, c, "normal", "")
        assert out.verdict == "APPROVE" and out.review_mode == "CACHE" and out.cache_hit
        assert out.precheck_age_ms is not None
    asyncio.run(run())


def test_material_change_uses_micro_review_not_full_review(tmp_path, monkeypatch):
    async def run():
        e = engine(tmp_path, monkeypatch)
        prior_snap = snapshot(phase="ARMED", trend15="bullish")
        call_a, put = score(phase="ARMED", total=84), score("PUT", phase="NONE", total=12)
        c = option()
        vector = e._ai_vector(prior_snap, call_a, put, c, "normal", "")
        fp = e.ai.feature_fingerprint(vector)
        reviewed = datetime.now(timezone.utc)
        pre = AIReview(verdict="APPROVE", confidence=.8, summary="coherent", reason_code="CLEAN_ALIGNMENT", review_mode="PRECHECK", reviewed_at=reviewed, feature_fingerprint=fp)
        e.ai_prechecks["AAPL"] = _AIPrecheckEntry(reviewed, reviewed+timedelta(seconds=25), vector, fp, pre, c.contract_symbol, c.ask, c.spread_pct)
        current = snapshot(phase="TRIGGERED", trend15="bearish")
        current.timestamp = prior_snap.timestamp
        call_t = score(phase="TRIGGERED", total=84)
        called = []
        async def micro(v, prior, changes):
            called.extend(changes)
            return AIReview(verdict="VETO", confidence=.9, summary="15m conflict", reason_code="HIGHER_TIMEFRAME_CONFLICT", review_mode="MICRO", reviewed_at=datetime.now(timezone.utc))
        async def full(*a, **k):
            raise AssertionError("full review should not run when a precheck exists")
        e.ai.micro_review = micro; e.ai.review = full
        out = await e._resolve_ai_final_review(current, call_t, put, c, "normal", "")
        assert out.review_mode == "MICRO" and out.verdict == "VETO"
        assert "trend_15m_changed" in called
    asyncio.run(run())


def test_expired_precheck_becomes_micro_review_context(tmp_path, monkeypatch):
    async def run():
        e = engine(tmp_path, monkeypatch)
        snap = snapshot(phase="TRIGGERED")
        call, put, c = score(phase="TRIGGERED", total=84), score("PUT", phase="NONE", total=12), option()
        vector = e._ai_vector(snap, call, put, c, "normal", "")
        fp = e.ai.feature_fingerprint(vector)
        reviewed = datetime.now(timezone.utc) - timedelta(seconds=40)
        pre = AIReview(verdict="APPROVE", confidence=.8, summary="old", reason_code="CLEAN_ALIGNMENT", review_mode="PRECHECK", reviewed_at=reviewed, feature_fingerprint=fp)
        e.ai_prechecks["AAPL"] = _AIPrecheckEntry(reviewed, reviewed+timedelta(seconds=25), vector, fp, pre, c.contract_symbol, c.ask, c.spread_pct)
        seen=[]
        async def micro(v, prior, changes):
            seen.extend(changes)
            return AIReview(verdict="APPROVE", confidence=.7, summary="still coherent", reason_code="CLEAN_ALIGNMENT", review_mode="MICRO", reviewed_at=datetime.now(timezone.utc))
        e.ai.micro_review = micro
        out = await e._resolve_ai_final_review(snap, call, put, c, "normal", "")
        assert out.verdict == "APPROVE" and out.review_mode == "MICRO"
        assert "precheck_cache_expired" in seen
    asyncio.run(run())


def test_veto_counterfactual_is_internal_research_only(tmp_path, monkeypatch):
    e = engine(tmp_path, monkeypatch)
    snap = snapshot(phase="TRIGGERED")
    call, put, c = score(phase="TRIGGERED", total=90), score("PUT", phase="NONE", total=10), option()
    review = AIReview(verdict="VETO", confidence=.9, summary="chop", reason_code="CHOP_RISK", review_mode="MICRO")
    e._record_ai_counterfactual(snap, call, put, c, review)
    assert len(e.ai_counterfactuals) == 1
    assert e.alerts == [] and e.tracked_trades == []
    rows = e.repo.counterfactual_rows()
    assert len(rows) == 1 and rows[0]["cf_ai_reason_code"] == "CHOP_RISK"


def test_fresh_armed_veto_is_rechecked_at_trigger_instead_of_reused(tmp_path, monkeypatch):
    async def run():
        e = engine(tmp_path, monkeypatch)
        snap = snapshot(phase="TRIGGERED")
        call, put, c = score(phase="TRIGGERED", total=88), score("PUT", phase="NONE", total=12), option()
        vector = e._ai_vector(snap, call, put, c, "normal", "")
        fp = e.ai.feature_fingerprint(vector)
        reviewed = datetime.now(timezone.utc)
        pre = AIReview(verdict="VETO", confidence=.8, summary="armed context weak", reason_code="WEAK_PARTICIPATION", review_mode="PRECHECK", reviewed_at=reviewed, feature_fingerprint=fp)
        e.ai_prechecks["AAPL"] = _AIPrecheckEntry(reviewed, reviewed+timedelta(seconds=25), vector, fp, pre, c.contract_symbol, c.ask, c.spread_pct)
        seen=[]
        async def micro(v, prior, changes):
            seen.extend(changes)
            return AIReview(verdict="APPROVE", confidence=.7, summary="trigger improved", reason_code="CLEAN_ALIGNMENT", review_mode="MICRO", reviewed_at=datetime.now(timezone.utc))
        e.ai.micro_review = micro
        out = await e._resolve_ai_final_review(snap, call, put, c, "normal", "")
        assert out.verdict == "APPROVE" and out.review_mode == "MICRO"
        assert "armed_precheck_veto_requires_trigger_recheck" in seen
    asyncio.run(run())
