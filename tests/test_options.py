from datetime import date
from app.options.scorer import OptionScorer

STRATEGY={"options":{"min_dte":0,"max_dte":14,"hard_max_ask":2.5,"preferred_max_ask":2.0,"min_mid":0.1,"max_spread_pct_0dte":18,"max_spread_pct_1_14dte":12,"preferred_delta_low":0.35,"preferred_delta_high":0.60,"max_candidates":5},"risk":{"premium_hard_stop_pct":0.20,"max_estimated_loss_usd":200}}

def test_0dte_missing_greeks_is_allowed():
    s=OptionScorer(STRATEGY)
    snap={"latestQuote":{"bp":1.40,"ap":1.46},"dailyBar":{"v":1000}}
    c=s.from_snapshot("SPY","CALL","SPY260807C00690000",snap,today=date(2026,8,7))
    assert c is not None and c.dte == 0 and c.delta is None

def test_expensive_contract_rejected():
    s=OptionScorer(STRATEGY)
    snap={"latestQuote":{"bp":2.50,"ap":2.70}}
    assert s.from_snapshot("SPY","CALL","SPY260807C00690000",snap,today=date(2026,8,7)) is None
