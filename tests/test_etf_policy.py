from types import SimpleNamespace

from app.config import load_settings
from app.engine import TradingEngine
from app.models import DirectionScore


def score(direction="CALL", total=90, other=20, chase=.2):
    return DirectionScore(
        symbol="SPY", direction=direction, total=total, trend=20, structure=20, volume=12,
        momentum=12, levels=8, market_context=7, setup="BREAKOUT_IGNITION", setup_score=90,
        breakout_phase="TRIGGERED", early_entry_eligible=True, chase_utilization=chase,
    )


def snap(**overrides):
    data = dict(
        session="prime", relative_volume=1.5, market_regime="TRENDING",
        trend_5m="bullish", trend_15m="bullish",
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def test_spy_qqq_iwm_are_tradable_but_use_stricter_etf_policy():
    engine = TradingEngine(load_settings())
    etfs = set(engine.settings.strategy["signals"]["etf_symbols"])
    assert etfs == {"SPY", "QQQ", "IWM"}
    assert "watch_only_symbols" not in engine.settings.strategy["signals"]


def test_good_etf_setup_is_allowed():
    engine = TradingEngine(load_settings())
    chosen = score(total=92, chase=.30)
    other = score(direction="PUT", total=20, chase=None)
    assert engine._etf_reject_reason("SPY", snap(), chosen, other) is None


def test_etf_rejects_weak_score_or_edge():
    engine = TradingEngine(load_settings())
    other = score(direction="PUT", total=25, chase=None)
    assert "score" in engine._etf_reject_reason("QQQ", snap(), score(total=82), other).lower()
    assert "edge" in engine._etf_reject_reason("IWM", snap(), score(total=88), score(direction="PUT", total=30, chase=None)).lower()


def test_etf_requires_trending_aligned_regime_rvol_and_earlier_entry():
    engine = TradingEngine(load_settings())
    chosen = score(total=94, chase=.30)
    other = score(direction="PUT", total=10, chase=None)
    assert "rvol" in engine._etf_reject_reason("SPY", snap(relative_volume=1.1), chosen, other).lower()
    assert "trending" in engine._etf_reject_reason("SPY", snap(market_regime="NEUTRAL"), chosen, other).lower()
    assert "5m/15m" in engine._etf_reject_reason("SPY", snap(trend_15m="neutral"), chosen, other)
    assert "late" in engine._etf_reject_reason("SPY", snap(), score(total=94, chase=.65), other).lower()


def test_etf_filter_does_not_apply_to_single_stock():
    engine = TradingEngine(load_settings())
    chosen = score(total=80, chase=.70)
    other = score(direction="PUT", total=20, chase=None)
    assert engine._etf_reject_reason("NVDA", snap(market_regime="NEUTRAL", relative_volume=1.0), chosen, other) is None


def test_etf_score_gate_increases_during_high_macro_risk():
    engine = TradingEngine(load_settings())
    chosen = score(total=88, chase=.30)
    other = score(direction="PUT", total=10, chase=None)
    assert engine._etf_reject_reason("SPY", snap(), chosen, other, "normal") is None
    reason = engine._etf_reject_reason("SPY", snap(), chosen, other, "high")
    assert reason is not None and "90" in reason
