from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import httpx
import websockets

from app.models import Bar


class AlpacaDataClient:
    STOCKS_BASE = "https://data.alpaca.markets/v2/stocks"
    OPTIONS_BASE = "https://data.alpaca.markets/v1beta1/options"

    def __init__(self, api_key: str, api_secret: str, stock_feed: str = "iex", option_feed: str = "indicative"):
        if not api_key or not api_secret:
            raise ValueError("ALPACA_API_KEY and ALPACA_API_SECRET are required in live mode")
        self.headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
        self.api_key = api_key
        self.api_secret = api_secret
        self.stock_feed = stock_feed
        self.option_feed = option_feed

    async def bootstrap_bars(self, symbols: list[str], days: int = 7) -> list[Bar]:
        # Look back across several calendar days so a Monday/opening-hour startup still
        # has enough prior-session bars for EMA/RSI/MACD initialization. End 16 minutes
        # behind now to remain compatible with Basic-plan recent historical restrictions.
        end = datetime.now(timezone.utc) - timedelta(minutes=16)
        start = end - timedelta(days=days)
        params = {
            "symbols": ",".join(symbols), "timeframe": "1Min", "start": start.isoformat(),
            "end": end.isoformat(), "limit": 10000, "feed": self.stock_feed, "adjustment": "raw", "sort": "asc",
        }
        out: list[Bar] = []
        page_token = None
        async with httpx.AsyncClient(timeout=30) as client:
            for _ in range(10):
                q = dict(params)
                if page_token:
                    q["page_token"] = page_token
                r = await client.get(f"{self.STOCKS_BASE}/bars", headers=self.headers, params=q)
                r.raise_for_status()
                data = r.json()
                payload = data.get("bars", {})
                for symbol, bars in payload.items():
                    for b in bars:
                        out.append(Bar(symbol=symbol, timestamp=b["t"], open=b["o"], high=b["h"], low=b["l"], close=b["c"], volume=b["v"]))
                page_token = data.get("next_page_token")
                if not page_token:
                    break
        return sorted(out, key=lambda x: x.timestamp)

    async def stream_bars(self, symbols: list[str]) -> AsyncIterator[Bar]:
        url = f"wss://stream.data.alpaca.markets/v2/{self.stock_feed}"
        backoff = 1
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({"action": "auth", "key": self.api_key, "secret": self.api_secret}))
                    await ws.recv()
                    await ws.send(json.dumps({"action": "subscribe", "bars": symbols, "updatedBars": symbols}))
                    backoff = 1
                    async for raw in ws:
                        messages = json.loads(raw)
                        for m in messages:
                            if m.get("T") in {"b", "u"} and m.get("S") in symbols:
                                yield Bar(symbol=m["S"], timestamp=m["t"], open=m["o"], high=m["h"], low=m["l"], close=m["c"], volume=m["v"])
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def get_latest_trade(self, symbol: str) -> tuple[float, datetime | None]:
        """Fetch the latest stock/ETF trade for post-review entry freshness checks."""
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{self.STOCKS_BASE}/{symbol}/trades/latest",
                headers=self.headers,
                params={"feed": self.stock_feed},
            )
            response.raise_for_status()
            trade = response.json().get("trade", {})
        price = float(trade["p"])
        ts = trade.get("t")
        when = datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if ts else None
        return price, when

    async def get_option_snapshots(self, contract_symbols: list[str]) -> dict[str, dict]:
        """Fetch latest option quote/trade/Greeks for already-selected contracts.

        Alpaca supports up to 100 contract symbols per request. V1.2 uses this for
        minute-by-minute monitoring of the small number of READY recommendations.
        """
        symbols = [s for s in dict.fromkeys(contract_symbols) if s]
        if not symbols:
            return {}
        out: dict[str, dict] = {}
        async with httpx.AsyncClient(timeout=15) as client:
            for i in range(0, len(symbols), 100):
                batch = symbols[i:i+100]
                r = await client.get(
                    f"{self.OPTIONS_BASE}/snapshots",
                    headers=self.headers,
                    params={"symbols": ",".join(batch), "feed": self.option_feed, "limit": 100},
                )
                r.raise_for_status()
                data = r.json()
                out.update(data.get("snapshots", data if isinstance(data, dict) else {}))
        return out

    async def get_option_candidates(self, underlying: str, direction: str, underlying_price: float, min_dte: int, max_dte: int, strike_window_pct: float) -> dict[str, dict]:
        today = datetime.now(timezone.utc).date()
        end = today + timedelta(days=max_dte)
        typ = "call" if direction == "CALL" else "put"
        low = underlying_price * (1 - strike_window_pct)
        high = underlying_price * (1 + strike_window_pct)
        params = {
            "feed": self.option_feed,
            "type": typ,
            "expiration_date_gte": today.isoformat(),
            "expiration_date_lte": end.isoformat(),
            "strike_price_gte": round(low, 2),
            "strike_price_lte": round(high, 2),
            "limit": 1000,
        }
        snapshots: dict[str, dict] = {}
        page_token = None
        async with httpx.AsyncClient(timeout=20) as client:
            for _ in range(3):
                q = dict(params)
                if page_token:
                    q["page_token"] = page_token
                r = await client.get(f"{self.OPTIONS_BASE}/snapshots/{underlying}", headers=self.headers, params=q)
                r.raise_for_status()
                data = r.json()
                snapshots.update(data.get("snapshots", {}))
                page_token = data.get("next_page_token")
                if not page_token:
                    break
        return snapshots
