from datetime import datetime, timedelta, timezone

from app.config import load_settings
from app.engine import TradingEngine
from app.models import DirectionScore, TradeAlert


def score(direction="CALL", total=90, other=20, setup="BREAKOUT_IGNITION", phase="TRIGGERED", chase=.2):
    return DirectionScore(
        symbol="NVDA", direction=direction, total=total, trend=20, structure=20, volume=12,
        momentum=12, levels=8, market_context=7, setup=setup, setup_score=90,
        breakout_phase=phase, early_entry_eligible=(setup == "BREAKOUT_IGNITION" and phase == "TRIGGERED"),
        chase_utilization=chase,
    )


def test_default_v132_caps_real_ready_alerts_at_six():
    engine = TradingEngine(load_settings())
    assert engine._max_alerts_per_day() == 6
    engine.alert_count_by_date["2026-08-17"] = 5
    assert engine._daily_alert_cap_reached("2026-08-17") is False
    engine.alert_count_by_date["2026-08-17"] = 6
    assert engine._daily_alert_cap_reached("2026-08-17") is True


def test_extended_is_rejected_for_every_setup_not_only_breakout_ignition():
    engine = TradingEngine(load_settings())
    s = score(setup="VWAP_RECLAIM_REJECTION", phase="EXTENDED", chase=None)
    assert "EXTENDED" in engine._entry_reject_reason(s)


def test_chase_utilization_filter_rejects_late_entry():
    engine = TradingEngine(load_settings())
    s = score(chase=.80)
    reason = engine._entry_reject_reason(s)
    assert reason is not None
    assert "80%" in reason


def test_profit_lock_requires_exceptional_follow_on_setup(monkeypatch):
    engine = TradingEngine(load_settings())
    monkeypatch.setattr(engine, "_wins_today", lambda day: 3)
    weak = score(total=88, setup="BREAKOUT_IGNITION")
    other = score(direction="PUT", total=15, setup="X", phase="NONE", chase=None)
    assert engine._profit_lock_reject_reason("2026-08-17", weak, other) is not None
    strong = score(total=94, setup="BREAKOUT_IGNITION")
    assert engine._profit_lock_reject_reason("2026-08-17", strong, other) is None


def test_four_winners_stops_new_real_recommendations(monkeypatch):
    engine = TradingEngine(load_settings())
    monkeypatch.setattr(engine, "_wins_today", lambda day: 4)
    assert engine._win_stop_reached("2026-08-17") is True


def test_second_same_symbol_signal_requires_wait_and_new_structure(monkeypatch):
    engine = TradingEngine(load_settings())
    now = datetime(2026, 8, 17, 14, 30, tzinfo=timezone.utc)
    previous = TradeAlert(
        id="old", created_at=now-timedelta(minutes=10), symbol="NVDA", session="prime", direction="CALL",
        quant_score=94, opposite_score=15, setup="BREAKOUT_IGNITION", reasons=[], underlying_price=180,
        confirmation="", invalidation_underlying=179, target_underlying=182, take_profit_pct=.3,
        emergency_stop_pct=.2, trigger_underlying=180, status="READY",
    )
    engine.alerts.insert(0, previous)
    chosen = score(total=95)
    chosen.trigger_underlying = 181
    other = score(direction="PUT", total=15, setup="X", phase="NONE", chase=None)

    class Snap:
        atr = 1.0
        trend_5m = "bullish"
        trend_15m = "bullish"

    day = engine._day_key(now)
    reason = engine._repeat_symbol_reject_reason("NVDA", day, now, chosen, other, Snap())
    assert reason is not None and "30 minutes" in reason
