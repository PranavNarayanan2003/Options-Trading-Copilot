from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import Bar, OptionCandidate


class DemoFeed:
    """Deterministic-ish fake market so the whole dashboard runs before API keys exist."""
    def __init__(self, symbols: list[str], seed: int = 7):
        self.symbols = symbols
        self.rng = random.Random(seed)
        base = {"SPY": 690, "QQQ": 610, "IWM": 250, "AAPL": 235, "MSFT": 525, "NVDA": 205, "GOOGL": 330, "AMZN": 245, "META": 780, "TSLA": 390}
        self.price = {s: base.get(s, 100) for s in symbols}
        ny = ZoneInfo("America/New_York")
        # A weekday synthetic session, independent of today's real market status.
        self.start = datetime(2026, 8, 7, 9, 0, tzinfo=ny)
        self.i = 0

    def historical(self, n: int = 90) -> list[Bar]:
        out = []
        for i in range(n):
            ts = self.start + timedelta(minutes=i)
            for j, s in enumerate(self.symbols):
                out.append(self._make_bar(s, ts, i, j))
        self.i = n
        return out

    def _make_bar(self, symbol: str, ts: datetime, i: int, j: int) -> Bar:
        p = self.price[symbol]
        # Produce a bullish breakout for SPY/QQQ/NVDA and mixed conditions elsewhere around 10:35.
        impulse = 0.0
        if symbol in {"SPY", "QQQ", "NVDA"} and 55 <= i <= 105:
            impulse = 0.00075
        elif symbol in {"IWM", "TSLA"} and 65 <= i <= 100:
            impulse = -0.00065
        cyc = math.sin((i + j * 3) / 8) * 0.00025
        noise = self.rng.gauss(0, 0.00032)
        ret = impulse + cyc + noise
        close = max(1, p * (1 + ret))
        wiggle = max(0.01, p * abs(self.rng.gauss(0.00035, 0.00012)))
        high = max(p, close) + wiggle
        low = min(p, close) - wiggle
        vol_multiplier = 4.0 if symbol in {"SPY", "QQQ", "NVDA", "IWM", "TSLA"} and 115 <= i <= 123 else 1.0
        vol = max(1000, int(50000 * (1 + abs(impulse) * 1200 + self.rng.random()) * vol_multiplier))
        self.price[symbol] = close
        return Bar(symbol=symbol, timestamp=ts, open=p, high=high, low=low, close=close, volume=vol)

    async def stream(self):
        while True:
            ts = self.start + timedelta(minutes=self.i)
            for j, s in enumerate(self.symbols):
                yield self._make_bar(s, ts, self.i, j)
            self.i += 1
            await asyncio.sleep(0.35)

    def option_candidates(self, underlying: str, direction: str, underlying_price: float) -> list[OptionCandidate]:
        today = (self.start + timedelta(minutes=self.i)).date()
        cp = "C" if direction == "CALL" else "P"
        candidates = []
        for dte, delta_abs, ask in [(0, None, 1.38), (1, 0.48, 1.72), (3, 0.43, 2.05), (7, 0.39, 2.42)]:
            expiry = today + timedelta(days=dte)
            strike = round(underlying_price)
            occ = f"{underlying}{expiry.strftime('%y%m%d')}{cp}{int(strike*1000):08d}"
            spread = 0.05 if ask < 2 else 0.08
            bid = ask - spread
            mid = (bid + ask) / 2
            candidates.append(OptionCandidate(
                symbol=underlying, contract_symbol=occ, direction=direction, expiration=expiry.isoformat(), dte=dte,
                strike=float(strike), bid=round(bid,2), ask=ask, mid=round(mid,2), spread_pct=round(spread/mid*100,1),
                delta=(delta_abs if direction == "CALL" else -delta_abs) if delta_abs is not None else None,
                gamma=None, theta=(-0.12 if dte else None), vega=None, iv=0.24 if dte else None,
                volume=800 + dte*120, option_score=84 - dte*1.5,
                estimated_emergency_risk_usd=round(ask*100*0.20, 2),
            ))
        return candidates
    def tracked_option_quote(self, alert, current_underlying: float) -> tuple[float, float]:
        """Synthetic bid/ask for demo tracking.

        This is intentionally simple: approximate the long option's dollar response
        from delta and the underlying move. It exists only to exercise monitoring UI,
        Telegram updates and state transitions before a market/API is available.
        """
        c = alert.contract
        if c is None:
            return (0.0, 0.0)
        direction = 1.0 if alert.direction == "CALL" else -1.0
        delta_abs = abs(c.delta) if c.delta is not None else (0.52 if c.dte == 0 else 0.45)
        underlying_move = (current_underlying - alert.underlying_price) * direction
        theoretical = max(0.05, c.ask + delta_abs * underlying_move)
        spread = max(0.03, c.ask - c.bid)
        bid = max(0.01, theoretical - spread * 0.55)
        ask = max(bid + 0.01, theoretical + spread * 0.45)
        return (round(bid, 2), round(ask, 2))

