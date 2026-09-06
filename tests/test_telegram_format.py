from datetime import datetime

from app.models import OptionCandidate, TradeAlert
from app.notifications.telegram import format_alert_html


def test_telegram_alert_explains_scores_and_dynamic_exit():
    contract = OptionCandidate(
        symbol="SPY", contract_symbol="SPY260810C00700000", direction="CALL",
        expiration="2026-08-10", dte=0, strike=700, bid=1.30, ask=1.35, mid=1.325,
        spread_pct=3.8, option_score=84, estimated_emergency_risk_usd=27,
    )
    alert = TradeAlert(
        id="x", created_at=datetime(2026,8,10,10,0), symbol="SPY", session="prime",
        direction="CALL", quant_score=92, opposite_score=35, setup="OPENING_RANGE_BREAK",
        reasons=["Price breaking opening-range high"], underlying_price=700,
        confirmation="hold", invalidation_underlying=698.7, target_underlying=702,
        contract=contract, take_profit_pct=.50, stretch_target_pct=.75, emergency_stop_pct=.20,
        premium_caution_pct=.10, conviction_label="STRONG", directional_edge=57,
        score_explanation="CALL evidence 92/100 vs opposite-side evidence 35/100 (directional edge +57).",
        pullback_hold_underlying=699.4, pullback_hold_label="opening-range high",
        key_level_price=701.0, key_level_label="session high", breakout_target_underlying=702.4,
        status="READY",
    )
    text = format_alert_html(alert)
    assert "CALL evidence 92/100" in text
    assert "not a probability of winning" in text
    assert "Primary option-profit zone: <b>+50%</b>" in text
    assert "Stretch zone: <b>+75%</b>" in text
    assert "<b>-10%</b> premium: caution only" in text
    assert "<b>-20%</b> premium: hard risk cap" in text


def test_telegram_ready_shows_ai_approval_and_webull_quote_source():
    from app.models import AIReview
    contract = OptionCandidate(
        symbol="AAPL", contract_symbol="AAPL260918C00200000", direction="CALL",
        expiration="2026-09-18", dte=1, strike=200, bid=1.10, ask=1.12, mid=1.11,
        spread_pct=1.8, option_score=90, estimated_emergency_risk_usd=22.4,
        quote_source="webull_openapi", alpaca_bid=1.00, alpaca_ask=1.02, webull_bid=1.10, webull_ask=1.12,
        quote_discrepancy_pct=9.9,
    )
    alert = TradeAlert(
        id="ai", created_at=datetime(2026,9,1,10,0), symbol="AAPL", session="prime",
        direction="CALL", quant_score=94, opposite_score=12, setup="BREAKOUT_IGNITION",
        reasons=["Fresh trigger", "Trend aligned"], underlying_price=200.1, confirmation="hold",
        invalidation_underlying=199.5, target_underlying=202, contract=contract,
        ai_review=AIReview(verdict="APPROVE", confidence=.91, summary="Trend, volume and structure remain coherent.", risk_flags=["0DTE sensitivity"]),
        take_profit_pct=.30, stretch_target_pct=.50, emergency_stop_pct=.20, premium_caution_pct=.10,
        conviction_label="HIGH", directional_edge=82, breakout_target_underlying=202,
        trigger_underlying=200, chase_limit_underlying=200.55, option_entry_reference=1.12,
        option_take_profit_price=1.46, option_stretch_price=1.68, option_caution_price=1.01,
        option_hard_stop_price=.90, status="READY",
    )
    text = format_alert_html(alert)
    assert "Quote source: <b>Webull OpenAPI</b>" in text
    assert "<b>AI FINAL CHECK</b>" in text
    assert "<b>APPROVED</b>" in text
    assert "review strength 91%" in text
    assert "AI review strength is not a probability of winning" in text
    assert "Alpaca indicative comparison" in text
