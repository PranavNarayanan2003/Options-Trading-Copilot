from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Any

from app.models import OptionCandidate


class OptionScorer:
    def __init__(self, strategy: dict):
        self.cfg = strategy["options"]
        self.risk = strategy["risk"]

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            x = float(value)
            return x if isfinite(x) else None
        except (TypeError, ValueError):
            return None

    def from_snapshot(self, underlying: str, direction: str, contract_symbol: str, snapshot: dict, today: date | None = None) -> OptionCandidate | None:
        today = today or date.today()
        # OCC symbol: root + YYMMDD + C/P + strike*1000. Root length varies.
        import re
        m = re.search(r"(\d{6})([CP])(\d{8})$", contract_symbol)
        if not m:
            return None
        expiry = datetime.strptime(m.group(1), "%y%m%d").date()
        dte = (expiry - today).days
        strike = int(m.group(3)) / 1000.0
        if dte < self.cfg["min_dte"] or dte > self.cfg["max_dte"]:
            return None
        if (direction == "CALL" and m.group(2) != "C") or (direction == "PUT" and m.group(2) != "P"):
            return None

        q = snapshot.get("latestQuote") or snapshot.get("latest_quote") or {}
        bid = self._num(q.get("bp", q.get("bid_price"))) or 0.0
        ask = self._num(q.get("ap", q.get("ask_price"))) or 0.0
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        mid = (bid + ask) / 2
        if mid < self.cfg["min_mid"] or ask > self.cfg["hard_max_ask"]:
            return None
        spread_pct = (ask - bid) / mid * 100
        max_spread = self.cfg["max_spread_pct_0dte"] if dte == 0 else self.cfg["max_spread_pct_1_14dte"]
        if spread_pct > max_spread:
            return None

        g = snapshot.get("greeks") or {}
        delta = self._num(g.get("delta"))
        gamma = self._num(g.get("gamma"))
        theta = self._num(g.get("theta"))
        vega = self._num(g.get("vega"))
        iv = self._num(snapshot.get("impliedVolatility", snapshot.get("implied_volatility")))
        daily = snapshot.get("dailyBar") or snapshot.get("daily_bar") or {}
        volume = self._num(daily.get("v", daily.get("volume")))

        score = 0.0
        # Price fit: user strongly prefers <= $2.00 but permits <= $2.50.
        score += 25 if ask <= self.cfg["preferred_max_ask"] else 15
        # Spread is the most important quality metric on a low-cost scalp contract.
        score += max(0, 30 * (1 - spread_pct / max_spread))
        # 0DTE: don't penalise missing Greeks; Alpaca may not calculate them.
        if dte == 0:
            score += 18
        else:
            abs_delta = abs(delta) if delta is not None else None
            if abs_delta is not None and self.cfg["preferred_delta_low"] <= abs_delta <= self.cfg["preferred_delta_high"]:
                score += 20
            elif abs_delta is not None and 0.25 <= abs_delta <= 0.70:
                score += 12
            else:
                score += 6
        if volume is not None:
            score += min(12, 3 + (volume ** 0.5) / 5)
        else:
            score += 4
        score += 8 if dte <= 3 else 6 if dte <= 7 else 4

        emergency_risk = ask * 100 * self.risk["premium_hard_stop_pct"]
        if emergency_risk > self.risk["max_estimated_loss_usd"]:
            return None

        return OptionCandidate(
            symbol=underlying,
            contract_symbol=contract_symbol,
            direction=direction,
            expiration=expiry.isoformat(),
            dte=dte,
            strike=strike,
            bid=round(bid, 2), ask=round(ask, 2), mid=round(mid, 2), spread_pct=round(spread_pct, 1),
            delta=delta, gamma=gamma, theta=theta, vega=vega, iv=iv, volume=volume,
            option_score=round(min(score, 100), 1),
            estimated_emergency_risk_usd=round(emergency_risk, 2),
        )

    def reprice(self, candidate: OptionCandidate, bid: float, ask: float) -> OptionCandidate | None:
        """Revalidate/re-score an already selected contract with a fresher broker quote."""
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        mid = (bid + ask) / 2
        if mid < self.cfg["min_mid"] or ask > self.cfg["hard_max_ask"]:
            return None
        max_spread = self.cfg["max_spread_pct_0dte"] if candidate.dte == 0 else self.cfg["max_spread_pct_1_14dte"]
        spread_pct = (ask - bid) / mid * 100
        if spread_pct > max_spread:
            return None

        score = 25 if ask <= self.cfg["preferred_max_ask"] else 15
        score += max(0, 30 * (1 - spread_pct / max_spread))
        if candidate.dte == 0:
            score += 18
        else:
            abs_delta = abs(candidate.delta) if candidate.delta is not None else None
            if abs_delta is not None and self.cfg["preferred_delta_low"] <= abs_delta <= self.cfg["preferred_delta_high"]:
                score += 20
            elif abs_delta is not None and 0.25 <= abs_delta <= 0.70:
                score += 12
            else:
                score += 6
        if candidate.volume is not None:
            score += min(12, 3 + (candidate.volume ** 0.5) / 5)
        else:
            score += 4
        score += 8 if candidate.dte <= 3 else 6 if candidate.dte <= 7 else 4

        emergency_risk = ask * 100 * self.risk["premium_hard_stop_pct"]
        if emergency_risk > self.risk["max_estimated_loss_usd"]:
            return None
        candidate.bid = round(bid, 2)
        candidate.ask = round(ask, 2)
        candidate.mid = round(mid, 2)
        candidate.spread_pct = round(spread_pct, 1)
        candidate.option_score = round(min(score, 100), 1)
        candidate.estimated_emergency_risk_usd = round(emergency_risk, 2)
        return candidate

    def rank(self, candidates: list[OptionCandidate], underlying_price: float) -> list[OptionCandidate]:
        # Small moneyness preference after option-quality score: avoid extremely far OTM cheap contracts.
        def key(c: OptionCandidate):
            moneyness = abs(c.strike - underlying_price) / max(underlying_price, 0.01)
            return (c.option_score - min(moneyness * 120, 12), -c.spread_pct, -c.ask)
        return sorted(candidates, key=key, reverse=True)[: self.cfg["max_candidates"]]
