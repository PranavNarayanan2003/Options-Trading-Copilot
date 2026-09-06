import asyncio

from app.execution.webull_readonly import WebullReadOnlyClient, _occ_symbol


def test_occ_symbol_matches_alpaca_compact_format():
    assert _occ_symbol("SPY", "2026-08-11", "CALL", 775.0) == "SPY260811C00775000"
    assert _occ_symbol("AAPL", "2026-08-14", "PUT", 242.5) == "AAPL260814P00242500"


def test_webull_signature_matches_official_hmac_sha1_worked_example():
    c = WebullReadOnlyClient(
        "776da210ab4a452795d74e726ebd74b6",
        "0f50a2e853334a9aae1a783bee120c1f",
        "account",
        "api.webull.com",
    )
    sig = c._build_signature(
        "/trade/place_order",
        {"a1": "webull", "a2": "123", "a3": "xxx", "q1": "yyy"},
        {"k1": 123, "k2": "this is the api request body", "k3": True, "k4": {"foo": [1, 2]}},
        "2022-01-04T03:55:31Z",
        "48ef5afed43d4d91ae514aaeafbc29ba",
    )
    assert sig == "kvlS6opdZDhEBo5jq40nHYXaLvM="


def test_webull_option_snapshot_parses_bid_ask_and_greeks():
    async def run():
        c = WebullReadOnlyClient("app", "secret", "account", "api.webull.com")
        called = {}

        async def fake_get(path, query=None):
            called["path"] = path; called["query"] = query
            return {"data": [{
                "symbol": "SPY260918C00600000", "bid": "1.23", "ask": "1.27", "price": "1.25",
                "volume": "421", "open_interest": "900", "delta": "0.48", "gamma": "0.03",
                "theta": "-0.05", "vega": "0.08", "imp_vol": "0.22", "bid_size": "18", "ask_size": "25",
            }]}

        c._get = fake_get
        quote = await c.get_option_snapshot("SPY260918C00600000")
        assert called["path"] == "/market-data/options/snapshots/list"
        assert called["query"] == {"symbols": "SPY260918C00600000", "category": "US_OPTION"}
        assert quote is not None
        assert quote.bid == 1.23 and quote.ask == 1.27
        assert quote.delta == 0.48 and quote.volume == 421
    asyncio.run(run())


def test_webull_positions_uses_current_trading_assets_endpoint():
    async def run():
        c = WebullReadOnlyClient("app", "secret", "acct", "api.webull.com")
        called = {}
        async def fake_get(path, query=None):
            called["path"] = path; called["query"] = query
            return {"data": []}
        c._get = fake_get
        positions = await c.get_positions()
        assert positions == []
        assert called["path"] == "/trading/assets/positions/list"
        assert called["query"] == {"account_id": "acct"}
    asyncio.run(run())
