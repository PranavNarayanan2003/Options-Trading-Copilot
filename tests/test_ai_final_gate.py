import asyncio
from datetime import datetime, timezone

from app.config import load_settings
from app.engine import TradingEngine
from app.models import AIReview, DirectionScore, OptionCandidate, TechnicalSnapshot, WebullOptionQuote


def _score(direction, total):
    return DirectionScore(
        symbol="AAPL", direction=direction, total=total, trend=24, structure=22, volume=13,
        momentum=14, levels=9, market_context=7, setup="BREAKOUT_IGNITION", setup_score=94,
        reasons=["fresh trigger", "higher-timeframe alignment"],
        invalidation_underlying=199.5 if direction == "CALL" else 200.5,
        target_underlying=202 if direction == "CALL" else 198,
        trigger_underlying=200.0, chase_limit_underlying=200.55 if direction == "CALL" else 199.45,
        breakout_phase="TRIGGERED", early_entry_eligible=True, chase_utilization=.25, regime_eligible=True,
    )


def _snapshot(ts):
    return TechnicalSnapshot(
        symbol="AAPL", timestamp=ts, session="prime", price=200.10,
        ema_fast=200.05, ema_medium=199.9, ema_slow=199.5, vwap=199.95, rsi=61,
        macd_hist=.2, macd_hist_change=.05, atr=1.0, relative_volume=1.7,
        volume_acceleration=1.3, trend_1m="bullish", trend_5m="bullish", trend_15m="bullish",
        breakout_phase_call="TRIGGERED", breakout_trigger_call=200.0, market_regime="TRENDING",
        trend_efficiency_5m=.62, vwap_crosses_recent=1, bars_ready=100,
    )


def _option():
    return OptionCandidate(
        symbol="AAPL", contract_symbol="AAPL260918C00200000", direction="CALL", expiration="2026-09-18",
        dte=1, strike=200, bid=1.00, ask=1.02, mid=1.01, spread_pct=2.0,
        option_score=85, estimated_emergency_risk_usd=20.4,
    )


def _engine(tmp_path, monkeypatch, *, webull=False, webull_required=False, strict_ai=False, db_name="ai.sqlite3"):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / db_name))
    monkeypatch.setenv("DATA_MODE", "live")
    monkeypatch.setenv("ENABLE_AI_REVIEW", "true")
    monkeypatch.setenv("AI_REQUIRED_FOR_READY", "true" if strict_ai else "false")
    monkeypatch.setenv("AI_FAIL_OPEN_ON_ERROR", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("ALPACA_API_KEY", "test-alpaca")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-alpaca-secret")
    monkeypatch.setenv("ENABLE_WEBULL_QUOTES", "true" if webull else "false")
    monkeypatch.setenv("WEBULL_QUOTES_REQUIRED_FOR_READY", "true" if webull_required else "false")
    if webull:
        monkeypatch.setenv("WEBULL_APP_KEY", "app")
        monkeypatch.setenv("WEBULL_APP_SECRET", "secret")
        monkeypatch.setenv("WEBULL_API_ENDPOINT", "api.webull.com")
    return TradingEngine(load_settings())


async def _prepare(e, verdict, confidence=.9):
    ts = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
    class FakeAlpaca:
        async def get_latest_trade(self, symbol):
            return 200.10, ts
        async def get_option_snapshots(self, symbols):
            return {symbols[0]: {"latestQuote": {"bp": 1.00, "ap": 1.02}}}
    e.alpaca = FakeAlpaca()
    snap = _snapshot(ts); call, put = _score("CALL", 94), _score("PUT", 12)
    e.latest["AAPL"] = snap
    e.scorer.score = lambda s, b: {"CALL": call, "PUT": put}
    e.news.symbol_risk_mode = lambda symbol, now: ("normal", "")
    async def choose(*args, **kwargs): return _option()
    e._select_option = choose
    async def review(*args, **kwargs):
        return AIReview(verdict=verdict, confidence=confidence, summary=f"{verdict} test", positives=["aligned"], risk_flags=[])
    e.ai.review = review
    sent = []
    async def send_alert(alert, trade): sent.append((alert, trade)); return 123
    e.telegram.send_alert = send_alert
    return ts, sent


def test_live_ai_approval_is_required_before_ready_and_telegram(tmp_path, monkeypatch):
    async def run():
        e = _engine(tmp_path, monkeypatch)
        ts, sent = await _prepare(e, "APPROVE", .91)
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert len(e.alerts) == 1 and e.alerts[0].status == "READY"
        assert e.alerts[0].ai_review.verdict == "APPROVE"
        assert len(e.tracked_trades) == 1 and len(sent) == 1
        assert e.alert_count_by_date[e._day_key(ts)] == 1
    asyncio.run(run())


def test_live_ai_veto_is_completely_silent(tmp_path, monkeypatch):
    async def run():
        e = _engine(tmp_path, monkeypatch)
        _, sent = await _prepare(e, "VETO", .88)
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert e.alerts == [] and e.tracked_trades == [] and sent == []
    asyncio.run(run())


def test_strict_live_ai_error_fails_closed(tmp_path, monkeypatch):
    async def run():
        e = _engine(tmp_path, monkeypatch, strict_ai=True, db_name="error.sqlite3")
        _, sent = await _prepare(e, "ERROR", 0.0)
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert e.alerts == [] and e.tracked_trades == [] and sent == []
    asyncio.run(run())


def test_ai_review_strength_is_not_a_win_probability_or_hard_gate(tmp_path, monkeypatch):
    async def run():
        e = _engine(tmp_path, monkeypatch, db_name="low-strength.sqlite3")
        _, sent = await _prepare(e, "APPROVE", 0.40)
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert len(e.alerts) == 1 and len(sent) == 1
        assert e.alerts[0].ai_review.verdict == "APPROVE"
        assert e.alerts[0].ai_review.confidence == 0.40
    asyncio.run(run())


def test_webull_quote_replaces_alpaca_reference_before_ai_review(tmp_path, monkeypatch):
    async def run():
        e = _engine(tmp_path, monkeypatch, webull=True, webull_required=True)
        _, sent = await _prepare(e, "APPROVE", .95)
        async def quote(symbol):
            return WebullOptionQuote(contract_symbol=symbol, bid=1.10, ask=1.12, delta=.5, volume=500)
        e.webull_client.get_option_snapshot = quote
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert len(sent) == 1
        alert = e.alerts[0]
        assert alert.contract.quote_source == "webull_openapi"
        assert alert.contract.alpaca_ask == 1.02
        assert alert.contract.webull_ask == 1.12
        assert alert.option_entry_reference == 1.12
    asyncio.run(run())


def test_required_webull_quote_failure_blocks_alert(tmp_path, monkeypatch):
    async def run():
        e = _engine(tmp_path, monkeypatch, webull=True, webull_required=True)
        _, sent = await _prepare(e, "APPROVE", .95)
        async def no_quote(symbol): return None
        e.webull_client.get_option_snapshot = no_quote
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert e.alerts == [] and e.tracked_trades == [] and sent == []
    asyncio.run(run())


def test_post_ai_underlying_chase_blocks_approved_candidate(tmp_path, monkeypatch):
    async def run():
        e = _engine(tmp_path, monkeypatch, db_name="chase.sqlite3")
        _, sent = await _prepare(e, "APPROVE", .95)
        class MovedAlpaca:
            async def get_latest_trade(self, symbol): return 200.60, datetime.now(timezone.utc)
            async def get_option_snapshots(self, symbols): return {symbols[0]: {"latestQuote": {"bp": 1.00, "ap": 1.02}}}
        e.alpaca = MovedAlpaca()
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert e.alerts == [] and sent == []
    asyncio.run(run())


def test_post_ai_option_move_blocks_stale_approved_candidate(tmp_path, monkeypatch):
    async def run():
        e = _engine(tmp_path, monkeypatch, db_name="optionmove.sqlite3")
        _, sent = await _prepare(e, "APPROVE", .95)
        class MovedOptionAlpaca:
            async def get_latest_trade(self, symbol): return 200.10, datetime.now(timezone.utc)
            async def get_option_snapshots(self, symbols): return {symbols[0]: {"latestQuote": {"bp": 1.18, "ap": 1.20}}}
        e.alpaca = MovedOptionAlpaca()
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert e.alerts == [] and sent == []
    asyncio.run(run())


def test_optional_ai_error_falls_back_to_deterministic_ready(tmp_path, monkeypatch):
    async def run():
        e = _engine(tmp_path, monkeypatch, strict_ai=False, db_name="optional-error.sqlite3")
        _, sent = await _prepare(e, "ERROR", 0.0)
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert len(e.alerts) == 1 and e.alerts[0].status == "READY"
        assert e.alerts[0].ai_review.verdict == "ERROR"
        assert len(e.tracked_trades) == 1 and len(sent) == 1
    asyncio.run(run())


def test_ai_disabled_live_mode_still_sends_deterministic_ready(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "no-ai.sqlite3"))
        monkeypatch.setenv("DATA_MODE", "live")
        monkeypatch.setenv("ENABLE_AI_REVIEW", "false")
        monkeypatch.setenv("AI_REQUIRED_FOR_READY", "false")
        monkeypatch.setenv("ALPACA_API_KEY", "test-alpaca")
        monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
        e = TradingEngine(load_settings())
        ts = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
        class FakeAlpaca:
            async def get_latest_trade(self, symbol): return 200.10, ts
            async def get_option_snapshots(self, symbols): return {symbols[0]: {"latestQuote": {"bp": 1.00, "ap": 1.02}}}
        e.alpaca = FakeAlpaca()
        snap = _snapshot(ts); call, put = _score("CALL", 94), _score("PUT", 12)
        e.latest["AAPL"] = snap
        e.scorer.score = lambda s, b: {"CALL": call, "PUT": put}
        e.news.symbol_risk_mode = lambda symbol, now: ("normal", "")
        async def choose(*args, **kwargs): return _option()
        e._select_option = choose
        sent=[]
        async def send_alert(alert, trade): sent.append((alert, trade)); return 123
        e.telegram.send_alert = send_alert
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert len(e.alerts) == 1 and e.alerts[0].status == "READY"
        assert e.alerts[0].ai_review.verdict == "SKIPPED"
        assert len(sent) == 1
    asyncio.run(run())


def test_strict_ai_disabled_blocks_live_ready(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "strict-no-ai.sqlite3"))
        monkeypatch.setenv("DATA_MODE", "live")
        monkeypatch.setenv("ENABLE_AI_REVIEW", "false")
        monkeypatch.setenv("AI_REQUIRED_FOR_READY", "true")
        monkeypatch.setenv("ALPACA_API_KEY", "test-alpaca")
        monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
        e = TradingEngine(load_settings())
        ts = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
        snap = _snapshot(ts); call, put = _score("CALL", 94), _score("PUT", 12)
        e.latest["AAPL"] = snap
        e.scorer.score = lambda s, b: {"CALL": call, "PUT": put}
        e.news.symbol_risk_mode = lambda symbol, now: ("normal", "")
        async def choose(*args, **kwargs): return _option()
        e._select_option = choose
        sent=[]
        async def send_alert(alert, trade): sent.append((alert, trade)); return 123
        e.telegram.send_alert = send_alert
        await e._evaluate("AAPL", refresh_snapshot=False)
        assert e.alerts == [] and sent == []
    asyncio.run(run())
