from __future__ import annotations

from dataclasses import dataclass

from app.models import DirectionScore, TechnicalSnapshot


@dataclass(frozen=True)
class ExitPlan:
    conviction_label: str
    directional_edge: float
    score_explanation: str
    primary_take_profit_pct: float
    stretch_take_profit_pct: float | None
    premium_caution_pct: float
    premium_hard_stop_pct: float
    pullback_hold_underlying: float
    pullback_hold_label: str
    key_level_price: float | None
    key_level_label: str | None
    breakout_target_underlying: float | None
    instructions: list[str]


class ExitPlanner:
    """Build a trade-specific exit plan from the underlying technical structure.

    The option premium is deliberately secondary to the underlying thesis. A small
    premium drawdown is a caution signal, not an automatic exit, while a hard
    premium cap prevents a fast option move from becoming an outsized loss.
    """

    def __init__(self, strategy: dict):
        self.cfg = strategy["risk"]

    @staticmethod
    def _nearest(values: list[tuple[str, float | None]], price: float, above: bool) -> tuple[str, float] | None:
        clean = [(name, float(v)) for name, v in values if v is not None and ((v > price) if above else (v < price))]
        if not clean:
            return None
        return min(clean, key=lambda x: abs(x[1] - price))

    def build(self, snapshot: TechnicalSnapshot, chosen: DirectionScore, other: DirectionScore) -> ExitPlan:
        direction = chosen.direction
        edge = round(chosen.total - other.total, 1)
        rvol = snapshot.relative_volume

        # Dynamic premium targets. These are profit-management zones, not promises.
        exceptional = chosen.total >= 95 and edge >= 45 and chosen.setup_score >= 90 and rvol >= 2.0
        strong = chosen.total >= 90 and edge >= 35 and chosen.setup_score >= 80 and rvol >= 1.5
        high = chosen.total >= 86 and edge >= 28
        if exceptional:
            label = "EXCEPTIONAL"
            primary_tp = self.cfg["exceptional_primary_take_profit_pct"]
            stretch = self.cfg["exceptional_stretch_take_profit_pct"]
        elif strong:
            label = "STRONG"
            primary_tp = self.cfg["strong_take_profit_pct"]
            stretch = self.cfg["strong_stretch_take_profit_pct"]
        elif high:
            label = "HIGH"
            primary_tp = self.cfg["high_take_profit_pct"]
            stretch = self.cfg["high_stretch_take_profit_pct"]
        else:
            label = "VALID"
            primary_tp = self.cfg["base_take_profit_pct"]
            stretch = None

        if direction == "CALL":
            support_values = [
                ("fast EMA", snapshot.ema_fast),
                ("VWAP", snapshot.vwap),
                ("medium EMA", snapshot.ema_medium),
                ("opening-range high", snapshot.opening_range_high),
                ("recent breakout level", snapshot.recent_high),
            ]
            resistance_values = [
                ("session high", snapshot.session_high),
                ("recent resistance", snapshot.recent_high),
                ("opening-range high", snapshot.opening_range_high),
                ("previous close", snapshot.previous_close),
            ]
            hold = self._nearest(support_values, snapshot.price, above=False)
            key = self._nearest(resistance_values, snapshot.price, above=True)
            hard_invalidation = float(chosen.invalidation_underlying or snapshot.price - snapshot.atr)
            pullback_level = hold[1] if hold else max(hard_invalidation, snapshot.price - 0.55 * snapshot.atr)
            pullback_label = hold[0] if hold else "short-term structure"
            extension = max(float(chosen.target_underlying or snapshot.price + snapshot.atr), snapshot.price + 2.4 * snapshot.atr)
            side_word = "above"
            fail_word = "below"
            resistance_word = "resistance"
        else:
            resistance_values = [
                ("fast EMA", snapshot.ema_fast),
                ("VWAP", snapshot.vwap),
                ("medium EMA", snapshot.ema_medium),
                ("opening-range low", snapshot.opening_range_low),
                ("recent breakdown level", snapshot.recent_low),
            ]
            support_values = [
                ("session low", snapshot.session_low),
                ("recent support", snapshot.recent_low),
                ("opening-range low", snapshot.opening_range_low),
                ("previous close", snapshot.previous_close),
            ]
            hold = self._nearest(resistance_values, snapshot.price, above=True)
            key = self._nearest(support_values, snapshot.price, above=False)
            hard_invalidation = float(chosen.invalidation_underlying or snapshot.price + snapshot.atr)
            pullback_level = hold[1] if hold else min(hard_invalidation, snapshot.price + 0.55 * snapshot.atr)
            pullback_label = hold[0] if hold else "short-term structure"
            extension = min(float(chosen.target_underlying or snapshot.price - snapshot.atr), snapshot.price - 2.4 * snapshot.atr)
            side_word = "below"
            fail_word = "above"
            resistance_word = "support"

        key_price = key[1] if key else float(chosen.target_underlying or extension)
        key_label = key[0] if key else f"model {resistance_word} target"

        caution = self.cfg["premium_caution_pct"]
        hard_stop = self.cfg["premium_hard_stop_pct"]
        score_explanation = (
            f"{direction} evidence {chosen.total:.0f}/100 vs opposite-side evidence {other.total:.0f}/100 "
            f"(directional edge +{edge:.0f})."
        )

        instructions = [
            f"A normal pullback is acceptable while the underlying holds {side_word} {pullback_label} near ${pullback_level:.2f} and the fast/medium EMA + VWAP structure remains supportive.",
            f"If the option falls about {caution*100:.0f}% but the underlying thesis is still intact, treat it as a warning rather than an automatic sell.",
            f"Exit the trade if the underlying confirms failure {fail_word} ${hard_invalidation:.2f}; do not wait for the option premium to recover.",
            f"Use -{hard_stop*100:.0f}% option premium as the hard risk cap if the option moves against you faster than the underlying signal can update.",
            f"Near {key_label} around ${key_price:.2f}, take profit if price rejects or momentum/RVOL fades.",
        ]
        if stretch is not None:
            instructions.append(
                f"If ${key_price:.2f} breaks and holds with expanding volume, do not mechanically exit at the first target; trail the position using the fast EMA / breakout level and allow the +{stretch*100:.0f}% stretch zone to remain in play."
            )
        else:
            instructions.append(
                "If the next key level breaks cleanly, trail under/over the fast EMA rather than turning a winning scalp into a losing trade."
            )

        return ExitPlan(
            conviction_label=label,
            directional_edge=edge,
            score_explanation=score_explanation,
            primary_take_profit_pct=primary_tp,
            stretch_take_profit_pct=stretch,
            premium_caution_pct=caution,
            premium_hard_stop_pct=hard_stop,
            pullback_hold_underlying=round(pullback_level, 2),
            pullback_hold_label=pullback_label,
            key_level_price=round(key_price, 2),
            key_level_label=key_label,
            breakout_target_underlying=round(extension, 2),
            instructions=instructions,
        )
