import asyncio
from types import SimpleNamespace

from app.config import load_settings
from app.engine import TradingEngine
from app.models import NewsItem
from datetime import datetime, timezone


def item(symbols):
    return NewsItem(
        id="n1", created_at=datetime.now(timezone.utc), headline="Test headline",
        source="test", symbols=symbols, impact="HIGH",
    )


def test_telegram_news_is_mag7_only_and_handles_goog_alias():
    engine = TradingEngine(load_settings())
    for sym in ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "GOOG"]:
        assert engine._telegram_news_relevant(item([sym])) is True
    for symbols in [[], ["SPY"], ["IWM"], ["QQQ"], ["AMD"], ["JPM"]]:
        assert engine._telegram_news_relevant(item(symbols)) is False


def test_non_mag7_high_news_does_not_send_telegram(monkeypatch):
    async def run():
        engine = TradingEngine(load_settings())
        sent = []
        async def fake_send(news):
            sent.append(news)
        monkeypatch.setattr(engine.telegram, "send_news", fake_send)
        await engine._on_high_news(item(["SPY"]))
        assert sent == []
        await engine._on_high_news(item(["NVDA"]))
        assert len(sent) == 1
    asyncio.run(run())
