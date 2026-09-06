from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from pathlib import Path
import yaml

from app.models import EconomicEvent, NewsItem
from app.news.intelligence import NewsIntelligence

STRATEGY=yaml.safe_load((Path(__file__).resolve().parents[1]/"strategy.yaml").read_text())
SETTINGS=SimpleNamespace(enable_news=True,alpaca_api_key="",alpaca_api_secret="",trading_economics_api_key="",economic_calendar_poll_seconds=600)

def intel(): return NewsIntelligence(SETTINGS,STRATEGY,["SPY","AAPL"])

def test_president_and_fed_headlines_are_high_impact():
    x=intel()
    assert x._classify("White House president signs executive order","",[],"test")[0]=="HIGH"
    assert x._classify("Federal Reserve chair discusses interest rates","",[],"test")[0]=="HIGH"

def test_scheduled_high_importance_macro_event_raises_risk():
    x=intel(); now=datetime.now(timezone.utc)
    x.events=[EconomicEvent(id="cpi",event_at=now+timedelta(minutes=10),name="Consumer Price Index",importance=3,source="BLS")]
    mode,reason=x.risk_mode(now)
    assert mode=="high" and "Consumer Price Index" in reason

def test_symbol_earnings_risk_is_symbol_specific():
    x=intel(); now=datetime.now(timezone.utc)
    x.events=[EconomicEvent(id="a",event_at=now+timedelta(minutes=30),name="AAPL earnings",importance=3,source="calendar",symbols=["AAPL"])]
    assert x.risk_mode(now)[0]=="normal"
    assert x.symbol_risk_mode("AAPL",now)[0]=="high"
    assert x.symbol_risk_mode("SPY",now)[0]=="normal"
