from datetime import datetime

from app.models import DirectionScore, OptionCandidate, TechnicalSnapshot, TradeAlert
from app.monitoring.tracker import TradeMonitor


def strategy():
    return {
        "risk": {"premium_caution_pct": .10, "premium_hard_stop_pct": .20},
        "monitoring": {"trail_min_direction_score": 78, "trail_min_direction_edge": 18, "trail_min_rvol": 1.15, "momentum_fade_edge": 12},
    }


def alert():
    c = OptionCandidate(
        symbol="SPY", contract_symbol="SPY260810C00700000", direction="CALL", expiration="2026-08-10",
        dte=0, strike=700, bid=1.30, ask=1.40, mid=1.35, spread_pct=7.4, option_score=84,
        estimated_emergency_risk_usd=28,
    )
    return TradeAlert(
        id="a1", created_at=datetime(2026,8,10,9,45), symbol="SPY", session="prime", direction="CALL",
        quant_score=90, opposite_score=30, setup="OPENING_RANGE_BREAK", reasons=["breakout"], underlying_price=700,
        confirmation="hold", invalidation_underlying=698.50, target_underlying=702.0, contract=c,
        take_profit_pct=.30, stretch_target_pct=.50, emergency_stop_pct=.20, premium_caution_pct=.10,
        conviction_label="HIGH", directional_edge=60, pullback_hold_underlying=699.2,
        pullback_hold_label="opening-range high", key_level_price=701.0, key_level_label="session high",
        breakout_target_underlying=702.4, status="READY",
    )


def snap(price=700, vwap=699.5, fast=699.8, medium=699.6, rvol=1.4, macd=.2):
    return TechnicalSnapshot(
        symbol="SPY", timestamp=datetime(2026,8,10,9,46), session="prime", price=price,
        ema_fast=fast, ema_medium=medium, ema_slow=699.0, vwap=vwap, rsi=60, macd_hist=macd,
        atr=1.0, relative_volume=rvol, session_high=701, recent_high=701, trend_1m="bullish",
        trend_5m="bullish", trend_15m="bullish", bars_ready=100,
    )


def scores(call=88, put=25):
    common = dict(symbol="SPY", trend=20, structure=20, volume=12, momentum=12, levels=8, market_context=8, setup="X", setup_score=85)
    return (
        DirectionScore(direction="CALL", total=call, **common),
        DirectionScore(direction="PUT", total=put, **common),
    )


def test_caution_does_not_exit_if_structure_holds():
    mon = TradeMonitor(strategy()); a = alert(); t = mon.start(a); cs, ps = scores()
    result = mon.evaluate(a, t, snap(price=699.7), cs, ps, option_bid=1.24, option_ask=1.28)
    assert result.trade.status == "CAUTION"
    assert result.trade.terminal is False
    assert result.update.event == "PREMIUM_CAUTION"
    assert "HOLD cautiously" in result.update.action


def test_underlying_invalidation_exits_even_before_premium_stop():
    mon = TradeMonitor(strategy()); a = alert(); t = mon.start(a); cs, ps = scores()
    result = mon.evaluate(a, t, snap(price=698.4, vwap=699.5, fast=699.0, medium=699.2), cs, ps, option_bid=1.32, option_ask=1.36)
    assert result.trade.status == "EXIT"
    assert result.trade.terminal is True
    assert result.update.event == "THESIS_INVALIDATED"


def test_key_level_break_switches_to_trailing():
    mon = TradeMonitor(strategy()); a = alert(); t = mon.start(a); cs, ps = scores(90, 25)
    result = mon.evaluate(a, t, snap(price=701.2, vwap=700, fast=700.8, medium=700.4, rvol=1.7), cs, ps, option_bid=1.62, option_ask=1.66)
    assert result.trade.status == "TRAILING"
    assert result.trade.key_level_broken is True
    assert result.trade.trailing_level_underlying is not None
    assert result.update.event == "KEY_LEVEL_BREAK"


def test_primary_target_trails_when_momentum_remains_strong():
    mon = TradeMonitor(strategy()); a = alert(); t = mon.start(a); cs, ps = scores(92, 20)
    result = mon.evaluate(a, t, snap(price=701.4, vwap=700, fast=701.0, medium=700.5, rvol=1.8), cs, ps, option_bid=1.85, option_ask=1.90)
    assert result.trade.primary_target_reached is True
    assert result.trade.status == "TRAILING"
    assert result.update.event == "PRIMARY_TARGET_TRAIL"


def test_hard_premium_stop_is_terminal():
    mon = TradeMonitor(strategy()); a = alert(); t = mon.start(a); cs, ps = scores()
    result = mon.evaluate(a, t, snap(price=699.6), cs, ps, option_bid=1.10, option_ask=1.14)
    assert result.trade.status == "EXIT"
    assert result.trade.terminal is True
    assert result.update.event == "PREMIUM_HARD_STOP"


def test_session_cutoff_exits_open_scalp():
    from zoneinfo import ZoneInfo
    mon = TradeMonitor({
        **strategy(),
        "session": {"timezone": "America/New_York", "secondary_end": "11:30"},
    })
    a = alert(); t = mon.start(a); cs, ps = scores()
    s = snap(price=700.2)
    s.timestamp = datetime(2026,8,10,12,0,tzinfo=ZoneInfo("America/New_York"))
    result = mon.evaluate(a, t, s, cs, ps, option_bid=1.42, option_ask=1.46)
    assert result.trade.terminal is True
    assert result.update.event == "SESSION_CUTOFF"


def test_fast_premium_poll_can_enforce_hard_stop_between_bars():
    from datetime import timezone
    mon = TradeMonitor({
        **strategy(),
        "session": {"timezone": "America/New_York", "secondary_end": "11:30"},
    })
    a = alert(); t = mon.start(a)
    result = mon.evaluate_premium_risk(
        a, t, option_bid=1.10, option_ask=1.12,
        timestamp=datetime(2026,8,10,13,46,15,tzinfo=timezone.utc),
    )
    assert result.trade.terminal is True
    assert result.update is not None
    assert result.update.event == "PREMIUM_HARD_STOP"
