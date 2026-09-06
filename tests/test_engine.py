import asyncio
from app.config import load_settings
from app.engine import TradingEngine


def test_demo_engine_builds_all_symbols():
    async def run():
        e=TradingEngine(load_settings())
        e.demo = __import__('app.data.demo', fromlist=['DemoFeed']).DemoFeed(e.settings.symbols)
        for b in e.demo.historical(100): e.state_store.add_bar(b)
        await e._refresh_all()
        assert set(e.latest) == set(e.settings.symbols)
        assert all(x in e.latest_scores for x in e.settings.symbols)
    asyncio.run(run())
