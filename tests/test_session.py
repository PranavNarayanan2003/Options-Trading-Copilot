from datetime import datetime
from zoneinfo import ZoneInfo
from app.market.session import SessionClock

CFG={"timezone":"America/New_York","prime_start":"09:30","prime_end":"10:30","secondary_end":"11:30"}

def test_session_windows():
    c=SessionClock(CFG); ny=ZoneInfo("America/New_York")
    assert c.classify(datetime(2026,8,7,9,45,tzinfo=ny)) == "prime"
    assert c.classify(datetime(2026,8,7,11,0,tzinfo=ny)) == "secondary"
    assert c.classify(datetime(2026,8,7,11,29,tzinfo=ny)) == "secondary"
    assert c.classify(datetime(2026,8,7,11,30,tzinfo=ny)) == "closed"
    assert c.classify(datetime(2026,8,7,12,0,tzinfo=ny)) == "closed"
