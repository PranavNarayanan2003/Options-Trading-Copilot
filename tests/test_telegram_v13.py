from datetime import datetime
from app.models import OptionCandidate, TradeAlert
from app.notifications.telegram import format_alert_html

def test_v13_alert_explicitly_names_strike_tp_and_stop_prices():
    c=OptionCandidate(symbol="SPY",contract_symbol="SPY260811C00775000",direction="CALL",expiration="2026-08-11",dte=0,strike=775,bid=1.36,ask=1.42,mid=1.39,spread_pct=4.3,option_score=88,estimated_emergency_risk_usd=28)
    a=TradeAlert(id="x",created_at=datetime(2026,8,11,10),symbol="SPY",session="prime",direction="CALL",quant_score=86,opposite_score=25,setup="BREAKOUT_IGNITION",reasons=["First clean break"],underlying_price=774.75,confirmation="hold",invalidation_underlying=773.96,target_underlying=776,contract=c,take_profit_pct=.30,stretch_target_pct=.50,emergency_stop_pct=.20,premium_caution_pct=.10,conviction_label="HIGH",directional_edge=61,breakout_target_underlying=776.2,trigger_underlying=774.62,chase_limit_underlying=775.18,option_entry_reference=1.42,option_take_profit_price=1.85,option_stretch_price=2.13,option_caution_price=1.28,option_hard_stop_price=1.14,status="READY")
    text=format_alert_html(a)
    assert "SPY 775 CALL" in text
    assert "TP1: <b>$1.85</b>" in text
    assert "Hard premium SL: <b>$1.14</b>" in text
    assert "Do not chase beyond" in text
    assert "Technical invalidation" in text
