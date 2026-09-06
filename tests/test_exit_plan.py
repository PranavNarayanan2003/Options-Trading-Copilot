from app.config import load_settings
from app.models import DirectionScore, TechnicalSnapshot
from app.risk.exit_plan import ExitPlanner
from datetime import datetime


def snapshot(rvol=1.8):
    return TechnicalSnapshot(
        symbol="SPY", timestamp=datetime(2026, 8, 10, 10, 0), session="prime", price=700.0,
        ema_fast=699.6, ema_medium=699.0, ema_slow=697.5, vwap=698.8, rsi=65,
        macd_hist=0.4, atr=1.0, relative_volume=rvol, opening_range_high=699.3,
        opening_range_low=696.0, session_high=700.8, session_low=696.0, previous_close=698.0,
        recent_high=700.8, recent_low=698.5, trend_1m="bullish", trend_5m="bullish",
        trend_15m="bullish", bars_ready=80,
    )


def score(total, direction="CALL", setup_score=90):
    return DirectionScore(
        symbol="SPY", direction=direction, total=total, trend=25, structure=25, volume=15,
        momentum=15, levels=10, market_context=10, setup="OPENING_RANGE_BREAK",
        setup_score=setup_score, invalidation_underlying=698.7, target_underlying=702.0,
    )


def test_strong_setup_gets_dynamic_target_and_tighter_risk():
    planner = ExitPlanner(load_settings().strategy)
    plan = planner.build(snapshot(), score(92), score(40, "PUT", 10))
    assert plan.conviction_label == "STRONG"
    assert plan.primary_take_profit_pct == 0.50
    assert plan.stretch_take_profit_pct == 0.75
    assert plan.premium_caution_pct == 0.10
    assert plan.premium_hard_stop_pct == 0.20
    assert plan.key_level_price is not None


def test_valid_setup_keeps_20pct_base_target():
    planner = ExitPlanner(load_settings().strategy)
    plan = planner.build(snapshot(rvol=1.2), score(83, setup_score=70), score(55, "PUT", 20))
    assert plan.conviction_label == "VALID"
    assert plan.primary_take_profit_pct == 0.20
    assert plan.stretch_take_profit_pct is None
