import asyncio
from datetime import datetime, timezone

from app.config import load_settings
from app.engine import TradingEngine
from app.models import DirectionScore, OptionCandidate, TechnicalSnapshot


def _score(direction, total):
    return DirectionScore(
        symbol="SPY", direction=direction, total=total, trend=22, structure=22, volume=13,
        momentum=13, levels=9, market_context=7, setup="BREAKOUT_IGNITION", setup_score=94,
        reasons=["fresh trigger"], invalidation_underlying=99.5 if direction == "CALL" else 100.5,
        target_underlying=102 if direction == "CALL" else 98, trigger_underlying=100,
        chase_limit_underlying=100.55 if direction == "CALL" else 99.45, breakout_phase="TRIGGERED",
        early_entry_eligible=True, chase_utilization=.30, regime_eligible=True,
    )


def _snapshot(ts):
    return TechnicalSnapshot(
        symbol="SPY", timestamp=ts, session="prime", price=100.10,
        ema_fast=100.05, ema_medium=99.9, ema_slow=99.5, vwap=99.95, rsi=60,
        macd_hist=.2, macd_hist_change=.05, atr=1.0, relative_volume=1.6,
        volume_acceleration=1.3, trend_1m="bullish", trend_5m="bullish", trend_15m="bullish",
        breakout_phase_call="TRIGGERED", breakout_trigger_call=100.0, market_regime="TRENDING",
        trend_efficiency_5m=.60, vwap_crosses_recent=1, bars_ready=100,
    )


def _option():
    return OptionCandidate(
        symbol="SPY", contract_symbol="SPY260817C00100000", direction="CALL", expiration="2026-08-17",
        dte=0, strike=100, bid=1.00, ask=1.02, mid=1.01, spread_pct=2.0,
        option_score=85, estimated_emergency_risk_usd=20.4,
    )


def test_qualifying_etf_is_real_ready_trade_and_telegram_alert(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "ready.sqlite3"))
        e = TradingEngine(load_settings())
        ts = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)
        snap = _snapshot(ts)
        call, put = _score("CALL", 94), _score("PUT", 10)
        e.latest["SPY"] = snap
        e.scorer.score = lambda s, b: {"CALL": call, "PUT": put}
        e.news.symbol_risk_mode = lambda symbol, now: ("normal", "")
        async def choose(*args, **kwargs): return _option()
        e._select_option = choose
        sent = []
        async def send_alert(alert, trade):
            sent.append((alert, trade))
            return 123
        e.telegram.send_alert = send_alert

        await e._evaluate("SPY", refresh_snapshot=False)

        assert len(e.alerts) == 1
        assert e.alerts[0].status == "READY"
        assert e.alerts[0].watch_only_reason == ""
        assert len(e.tracked_trades) == 1
        assert e.tracked_trades[0].shadow is False
        assert len(sent) == 1
        assert e.alert_count_by_date[e._day_key(ts)] == 1
    asyncio.run(run())


def test_etf_failing_stricter_gate_is_silent(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "reject.sqlite3"))
        e = TradingEngine(load_settings())
        ts = datetime(2026, 8, 17, 14, 0, tzinfo=timezone.utc)
        snap = _snapshot(ts)
        call, put = _score("CALL", 82), _score("PUT", 10)
        e.latest["SPY"] = snap
        e.scorer.score = lambda s, b: {"CALL": call, "PUT": put}
        e.news.symbol_risk_mode = lambda symbol, now: ("normal", "")
        sent = []
        async def send_alert(alert, trade): sent.append((alert, trade))
        e.telegram.send_alert = send_alert

        await e._evaluate("SPY", refresh_snapshot=False)

        assert e.alerts == []
        assert e.tracked_trades == []
        assert sent == []
    asyncio.run(run())
