from datetime import datetime, timezone
from pathlib import Path
import yaml

from app.models import TechnicalSnapshot
from app.signals.scorer import SignalScorer

STRATEGY=yaml.safe_load((Path(__file__).resolve().parents[1]/"strategy.yaml").read_text())

def snap(phase="TRIGGERED", price=101.05):
    return TechnicalSnapshot(symbol="SPY",timestamp=datetime(2026,8,11,13,35,tzinfo=timezone.utc),session="prime",price=price,
        ema_fast=100.95,ema_medium=100.80,ema_slow=100.50,vwap=100.70,rsi=60,macd_hist=.15,macd_hist_change=.04,atr=1.0,
        relative_volume=1.6,volume_acceleration=1.3,opening_range_high=101.0,opening_range_low=99.0,opening_range_complete=True,
        session_high=101.1,session_low=99.4,recent_high=100.98,recent_low=99.8,trend_1m="bullish",trend_5m="bullish",trend_15m="bullish",
        fast_ema_slope_atr=.08,medium_ema_slope_atr=.05,bar_body_ratio=.65,close_location=.88,
        breakout_trigger_call=101.0,breakout_trigger_put=99.0,breakout_distance_atr_call=.05,breakout_age_bars_call=0,
        breakout_phase_call=phase,bars_ready=200)

def test_fresh_trigger_prefers_breakout_ignition():
    score=SignalScorer(STRATEGY).score(snap())["CALL"]
    assert score.setup=="BREAKOUT_IGNITION"
    assert score.early_entry_eligible is True
    assert score.breakout_phase=="TRIGGERED"
    assert score.trigger_underlying==101.0
    assert score.chase_limit_underlying==101.55
    assert score.total>=72

def test_extended_breakout_is_not_early_entry():
    score=SignalScorer(STRATEGY).score(snap("EXTENDED",101.8))["CALL"]
    assert score.breakout_phase=="EXTENDED"
    assert score.early_entry_eligible is False
